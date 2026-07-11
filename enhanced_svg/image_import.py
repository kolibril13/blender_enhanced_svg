"""
Import of raster images embedded in (or referenced by) an SVG file.

Extraction runs on the *original* SVG content: the preprocessing step
(flatten_svg) deletes the <defs> section, which is where embedded
<image> elements usually live, so images must be collected before
preprocessing. Blender's own SVG importer ignores <image> elements
entirely.

Each image placement is turned into a four-vertex mesh plane whose
corners are the image rectangle transformed by the cumulative SVG
transform, mapped to Blender space with the exact same conventions as
Blender's bundled io_curve_svg importer, so planes line up with the
imported curves.
"""

import base64
import hashlib
import math
import os
import re
import tempfile
import urllib.parse
from pathlib import Path

from lxml import etree

from .svg_preprocessing import NS_MAP, parse_svg_string

XLINK_HREF = f"{{{NS_MAP['xlink']}}}href"

# Same unit table as io_curve_svg (90 dpi user units).
SVG_UNITS = {
    "": 1.0,
    "px": 1.0,
    "in": 90.0,
    "mm": 90.0 / 25.4,
    "cm": 90.0 / 2.54,
    "pt": 1.25,
    "pc": 15.0,
    "em": 1.0,
    "ex": 1.0,
}

# io_curve_svg maps 90 SVG user units to 1 inch (0.0254 m), Y pointing down.
BLENDER_SCALE = 1.0 / 90.0 * 0.3048 / 12.0

_FLOAT_RE = re.compile(r"[+-]?\d*\.?\d+(?:[eE][+-]?\d+)?")
_TRANSFORM_RE = re.compile(r"\s*([A-Za-z]+)\s*\((.*?)\)")
_DATA_URI_RE = re.compile(
    r"data:(?P<mime>[^;,]*)(?P<params>(?:;[^;,]*)*),(?P<data>.*)", re.DOTALL
)


# --- 2D affine matrices, stored as (a, b, c, d, e, f) like SVG's matrix():
#     x' = a*x + c*y + e
#     y' = b*x + d*y + f

MAT_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mat_mul(m, n):
    """Matrix product m @ n (n is applied to points first)."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def mat_apply(m, point):
    a, b, c, d, e, f = m
    x, y = point
    return (a * x + c * y + e, b * x + d * y + f)


def mat_translate(tx, ty):
    return (1.0, 0.0, 0.0, 1.0, tx, ty)


def mat_scale(sx, sy):
    return (sx, 0.0, 0.0, sy, 0.0, 0.0)


def parse_transform(transform):
    """Parse an SVG 'transform' attribute into a single matrix."""
    m = MAT_IDENTITY
    for match in _TRANSFORM_RE.finditer(transform):
        func = match.group(1)
        params = [float(p) for p in _FLOAT_RE.findall(match.group(2))]
        if func == "matrix" and len(params) == 6:
            t = tuple(params)
        elif func == "translate" and params:
            t = mat_translate(params[0], params[1] if len(params) > 1 else 0.0)
        elif func == "scale" and params:
            t = mat_scale(params[0], params[1] if len(params) > 1 else params[0])
        elif func == "rotate" and params:
            ang = math.radians(params[0])
            rot = (math.cos(ang), math.sin(ang), -math.sin(ang), math.cos(ang), 0.0, 0.0)
            if len(params) >= 3:
                cx, cy = params[1], params[2]
                t = mat_mul(mat_mul(mat_translate(cx, cy), rot), mat_translate(-cx, -cy))
            else:
                t = rot
        elif func == "skewX" and params:
            t = (1.0, 0.0, math.tan(math.radians(params[0])), 1.0, 0.0, 0.0)
        elif func == "skewY" and params:
            t = (1.0, math.tan(math.radians(params[0])), 0.0, 1.0, 0.0, 0.0)
        else:
            continue
        m = mat_mul(m, t)
    return m


def parse_coord(coord, size=0.0):
    """Parse a coordinate/length with optional unit, like io_curve_svg's SVGParseCoord."""
    coord = coord.strip()
    match = _FLOAT_RE.match(coord)
    if not match:
        return 0.0
    value = float(match.group(0))
    unit = coord[match.end():].strip()
    if unit == "%":
        return float(size) / 100.0 * value
    return value * SVG_UNITS.get(unit, 1.0)


def _root_matrix(root, scene_scale_length=1.0):
    """
    Compute the root <svg> viewport matrix (in SVG user coordinates),
    replicating io_curve_svg's SVGMatrixFromNode + document-origin logic
    so image planes land exactly where the imported curves do.
    """
    x = parse_coord(root.get("x", "0"))
    y = parse_coord(root.get("y", "0"))
    width_attr = root.get("width")
    height_attr = root.get("height")
    w = parse_coord(width_attr) if width_attr else 0.0
    h = parse_coord(height_attr) if height_attr else 0.0

    m = mat_translate(x, y)

    viewbox = None
    vb_attr = root.get("viewBox")
    if vb_attr:
        parts = [float(p) for p in _FLOAT_RE.findall(vb_attr)]
        if len(parts) == 4 and parts[2] != 0 and parts[3] != 0:
            viewbox = parts

    if viewbox:
        vx, vy, vw, vh = viewbox
        if w != 0 and h != 0:
            scale = min(w / vw, h / vh)
        else:
            scale = 1.0
            w, h = vw, vh
        tx = (w - vw * scale) / 2.0
        ty = (h - vh * scale) / 2.0
        m = mat_mul(m, mat_translate(tx, ty))
        m = mat_mul(m, mat_translate(-vx, -vy))
        m = mat_mul(m, mat_scale(scale, scale))

        # Unit matching for physically-sized documents (cm/mm/in/pt/pc).
        unit = ""
        if height_attr:
            match = _FLOAT_RE.match(height_attr.strip())
            if match:
                unit = height_attr.strip()[match.end():].strip()
        if unit in ("cm", "mm", "in", "pt", "pc"):
            unitscale = SVG_UNITS[unit] / 90.0 * 1000.0 / 39.3701
            unitscale = unitscale / scene_scale_length
            m = mat_mul(m, mat_scale(unitscale, unitscale))

        # Match document origin with the 3D space origin.
        m = mat_mul(m, mat_translate(0.0, -vy - vh))

    return m


def _decode_href(href, svg_dir, warnings):
    """Return (bytes, file_extension) for an image href, or (None, None)."""
    href = href.strip()
    if href.startswith("data:"):
        match = _DATA_URI_RE.match(href)
        if not match:
            warnings.append("Skipped image with malformed data URI")
            return None, None
        mime = match.group("mime").strip().lower()
        try:
            if "base64" in match.group("params"):
                data = base64.b64decode(re.sub(r"\s+", "", match.group("data")))
            else:
                data = urllib.parse.unquote_to_bytes(match.group("data"))
        except Exception:
            warnings.append("Skipped image with undecodable data URI")
            return None, None
        ext = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "image/bmp": ".bmp",
            "image/tiff": ".tif",
        }.get(mime, ".png")
        return data, ext

    # External file reference, resolved relative to the SVG's directory.
    if svg_dir is not None and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", href):
        path = Path(svg_dir) / urllib.parse.unquote(href)
        if path.is_file():
            return path.read_bytes(), (path.suffix or ".png")

    warnings.append(f"Could not resolve image reference: {href[:80]}")
    return None, None


def _emit_image(el, ctm, images, warnings, svg_dir):
    w = parse_coord(el.get("width", "0"))
    h = parse_coord(el.get("height", "0"))
    href = el.get(XLINK_HREF) or el.get("href")
    if not href or w <= 0 or h <= 0:
        return
    data, ext = _decode_href(href, svg_dir, warnings)
    if data is None:
        return
    x = parse_coord(el.get("x", "0"))
    y = parse_coord(el.get("y", "0"))
    # Corner order: top-left, top-right, bottom-right, bottom-left
    # (in SVG's y-down coordinates).
    corners = [
        mat_apply(ctm, p)
        for p in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
    ]
    name = el.get("id") or f"Image{len(images) + 1}"
    images.append({"name": name, "data": data, "ext": ext, "corners": corners})


# Definition-only / non-rendered containers: never render their children in place.
_SKIP_TAGS = {"defs", "symbol", "clipPath", "mask", "pattern", "style", "script"}


def _walk(el, ctm, ids, images, warnings, svg_dir, depth=0):
    if depth > 20 or not isinstance(el.tag, str):
        return
    tag = etree.QName(el.tag).localname
    if tag in _SKIP_TAGS:
        return

    transform = el.get("transform")
    if transform:
        ctm = mat_mul(ctm, parse_transform(transform))

    if tag == "image":
        _emit_image(el, ctm, images, warnings, svg_dir)
        return

    if tag == "use":
        href = el.get(XLINK_HREF) or el.get("href")
        if not href or not href.startswith("#"):
            return
        target = ids.get(href[1:])
        if target is None:
            return
        x = parse_coord(el.get("x", "0"))
        y = parse_coord(el.get("y", "0"))
        if x or y:
            ctm = mat_mul(ctm, mat_translate(x, y))
        if etree.QName(target.tag).localname == "symbol":
            # Instantiate the symbol's children (its viewBox is ignored,
            # matching flatten_svg's behavior for curve geometry).
            for child in target:
                _walk(child, ctm, ids, images, warnings, svg_dir, depth + 1)
        else:
            _walk(target, ctm, ids, images, warnings, svg_dir, depth + 1)
        return

    if tag == "svg":
        # Nested <svg>: apply its x/y offset.
        x = parse_coord(el.get("x", "0"))
        y = parse_coord(el.get("y", "0"))
        if x or y:
            ctm = mat_mul(ctm, mat_translate(x, y))

    for child in el:
        _walk(child, ctm, ids, images, warnings, svg_dir, depth + 1)


def extract_svg_images(svg_content, svg_dir=None, scene_scale_length=1.0):
    """
    Collect every rendered raster image placement from the SVG content.

    Handles both inline <image> elements and <use> references to <image>
    (or <symbol>/<g> wrapping images) defined in <defs>. Must be called on
    the original SVG content, before preprocessing strips <defs>.

    Returns (images, warnings) where each image is a dict with:
      name    - the image element's id (or a generated name)
      data    - decoded image bytes
      ext     - file extension guessed from the mime type
      corners - the image rectangle's four corners (TL, TR, BR, BL) in
                final SVG user space, ready to scale into Blender space.
    """
    root = parse_svg_string(svg_content)
    ids = {}
    for el in root.iter():
        if isinstance(el.tag, str):
            el_id = el.get("id")
            if el_id:
                ids[el_id] = el

    images = []
    warnings = []
    ctm = _root_matrix(root, scene_scale_length)
    for child in root:
        _walk(child, ctm, ids, images, warnings, svg_dir)
    return images, warnings


# --- Blender-side: image datablocks, materials and plane objects ---

def _load_packed_image(info, cache):
    """Create (or reuse) a packed image datablock from decoded bytes."""
    import bpy

    key = hashlib.sha1(info["data"]).hexdigest()
    if key in cache:
        return cache[key]

    tmp = tempfile.NamedTemporaryFile(suffix=info["ext"], delete=False)
    try:
        tmp.write(info["data"])
        tmp.close()
        image = bpy.data.images.load(tmp.name)
        image.name = info["name"]
        image.pack()
        image.filepath = ""
    except RuntimeError:
        return None
    finally:
        os.unlink(tmp.name)

    cache[key] = image
    return image


def _create_image_material(image, use_emission):
    """
    Create a material showing the image's color and alpha: a Transparent BSDF
    mixed with either a plain Diffuse BSDF or (for emission imports) an
    Emission shader modulated by the object 'opacity' attribute, matching
    the layout of create_material() in imports.py.
    """
    import bpy

    name = f"MatImg_{image.name}"
    existing_mat = bpy.data.materials.get(name)
    if existing_mat:
        return existing_mat

    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    mat.blend_method = "BLEND"
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    tex = nodes.new(type="ShaderNodeTexImage")
    tex.image = image
    tex.extension = "CLIP"
    transparent = nodes.new(type="ShaderNodeBsdfTransparent")
    mix_shader = nodes.new(type="ShaderNodeMixShader")
    output = nodes.new(type="ShaderNodeOutputMaterial")

    tex.location = (-600, 100)
    transparent.location = (-300, 200)
    mix_shader.location = (0, 100)
    output.location = (300, 100)

    if use_emission:
        shader = nodes.new(type="ShaderNodeEmission")
        shader.inputs["Strength"].default_value = 1.0

        attr_node = nodes.new("ShaderNodeAttribute")
        attr_node.attribute_name = "opacity"
        attr_node.attribute_type = "OBJECT"
        attr_node.location = (-600, 400)

        multiply = nodes.new(type="ShaderNodeMath")
        multiply.operation = "MULTIPLY"
        multiply.location = (-300, 400)

        links.new(tex.outputs["Alpha"], multiply.inputs[0])
        links.new(attr_node.outputs["Fac"], multiply.inputs[1])
        links.new(multiply.outputs[0], mix_shader.inputs["Fac"])
    else:
        shader = nodes.new(type="ShaderNodeBsdfDiffuse")
        links.new(tex.outputs["Alpha"], mix_shader.inputs["Fac"])

    shader.location = (-300, 0)
    links.new(tex.outputs["Color"], shader.inputs["Color"])
    links.new(transparent.outputs[0], mix_shader.inputs[1])
    links.new(shader.outputs[0], mix_shader.inputs[2])
    links.new(mix_shader.outputs[0], output.inputs["Surface"])

    return mat


def create_image_planes(images, collection, use_emission=False):
    """
    Add a textured plane object to the collection for each extracted image,
    placed to match the curves created by Blender's SVG importer.
    """
    import bpy

    created = []
    image_cache = {}
    for info in images:
        image = _load_packed_image(info, image_cache)
        if image is None:
            continue

        verts = [
            (cx * BLENDER_SCALE, -cy * BLENDER_SCALE, 0.0)
            for cx, cy in info["corners"]
        ]
        # Wind the face so its normal points up (+Z), even if the SVG
        # transform mirrored the rectangle.
        area = sum(
            verts[i][0] * verts[(i + 1) % 4][1] - verts[(i + 1) % 4][0] * verts[i][1]
            for i in range(4)
        )
        loop_order = (0, 1, 2, 3) if area > 0 else (0, 3, 2, 1)
        corner_uvs = {0: (0, 1), 1: (1, 1), 2: (1, 0), 3: (0, 0)}

        mesh = bpy.data.meshes.new(f"Image_{info['name']}")
        mesh.from_pydata(verts, [], [loop_order])
        uv_layer = mesh.uv_layers.new()
        for loop in mesh.loops:
            uv_layer.data[loop.index].uv = corner_uvs[loop.vertex_index]

        mesh.materials.append(_create_image_material(image, use_emission))

        obj = bpy.data.objects.new(f"Image_{info['name']}", mesh)
        collection.objects.link(obj)
        if use_emission:
            obj["opacity"] = 1.0
            obj.id_properties_ui("opacity").update(min=0.0, max=1.0, step=0.1)
        created.append(obj)

    return created
