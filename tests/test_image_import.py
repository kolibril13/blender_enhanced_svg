import base64
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

import bpy

import enhanced_svg
from enhanced_svg import imports as imports_module
from enhanced_svg.image_import import (
    BLENDER_SCALE,
    PAINT_ORDER_Z_STEP,
    _placement_geometry,
    create_image_planes,
    extract_svg_images,
    finalize_paint_order,
    prepare_svg_images,
)
from enhanced_svg.imports import (
    _select_import_collection,
    deduplicate_materials,
)
from enhanced_svg.svg_preprocessing import preprocess_svg


SVG_NS = "http://www.w3.org/2000/svg"
TINY_DATA_URI = "data:image/png;base64,AA=="


def _png_bytes(width=2, height=1):
    def chunk(kind, data):
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    row = b"\x00" + b"\xff\x40\x20\xff" * width
    pixels = row * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


def _data_uri(width=2, height=1):
    payload = base64.b64encode(_png_bytes(width, height)).decode("ascii")
    return f"data:image/png;base64,{payload}"


def _snapshot_blender_data():
    return {
        "objects": set(bpy.data.objects),
        "meshes": set(bpy.data.meshes),
        "curves": set(bpy.data.curves),
        "materials": set(bpy.data.materials),
        "images": set(bpy.data.images),
        "collections": set(bpy.data.collections),
    }


def _restore_blender_data(before):
    for obj in tuple(set(bpy.data.objects) - before["objects"]):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in tuple(set(bpy.data.meshes) - before["meshes"]):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for curve in tuple(set(bpy.data.curves) - before["curves"]):
        if curve.users == 0:
            bpy.data.curves.remove(curve)
    for material in tuple(set(bpy.data.materials) - before["materials"]):
        if material.users == 0:
            bpy.data.materials.remove(material)
    for image in tuple(set(bpy.data.images) - before["images"]):
        if image.users == 0:
            bpy.data.images.remove(image)
    for collection in tuple(set(bpy.data.collections) - before["collections"]):
        bpy.data.collections.remove(collection)


class ExtractionTests(unittest.TestCase):
    def test_percentage_lengths_use_active_viewport(self):
        svg = f'''<svg xmlns="{SVG_NS}" width="100" height="80">
          <image x="10%" y="25%" width="50%" height="50%" href="{TINY_DATA_URI}"/>
        </svg>'''
        images, warnings = extract_svg_images(svg)
        self.assertEqual(warnings, [])
        self.assertEqual(len(images), 1)
        self.assertEqual(
            images[0]["corners"],
            [(10.0, 20.0), (60.0, 20.0), (60.0, 60.0), (10.0, 60.0)],
        )

    def test_nested_svg_matches_blender_viewport_math(self):
        svg = f'''<svg xmlns="{SVG_NS}" width="200" height="200">
          <svg x="20" y="30" width="100" height="50" viewBox="0 0 10 10">
            <image width="10" height="10" href="{TINY_DATA_URI}"/>
          </svg>
        </svg>'''
        images, _warnings = extract_svg_images(svg)
        self.assertEqual(
            images[0]["corners"],
            [(32.5, 17.5), (57.5, 17.5), (57.5, 30.0), (32.5, 30.0)],
        )

    def test_zero_width_viewbox_keeps_blender_origin_convention(self):
        svg = f'''<svg xmlns="{SVG_NS}" width="200" height="100"
          viewBox="0 0 0 50">
          <image width="1" height="1" href="{TINY_DATA_URI}"/>
        </svg>'''
        images, _warnings = extract_svg_images(svg)
        self.assertEqual(
            images[0]["corners"],
            [(0.0, -50.0), (1.0, -50.0), (1.0, -49.0), (0.0, -49.0)],
        )

    def test_hidden_images_are_skipped_and_opacity_is_inherited(self):
        hidden = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <g display="none"><image width="10" height="10" href="{TINY_DATA_URI}"/></g>
        </svg>'''
        images, _warnings = extract_svg_images(hidden)
        self.assertEqual(images, [])

        translucent = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <g opacity="0.5"><image opacity="25%" width="10" height="10" href="{TINY_DATA_URI}"/></g>
        </svg>'''
        images, _warnings = extract_svg_images(translucent)
        self.assertAlmostEqual(images[0]["opacity"], 0.125)

        important = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <image style="display: none !important" width="10" height="10" href="{TINY_DATA_URI}"/>
        </svg>'''
        images, _warnings = extract_svg_images(important)
        self.assertEqual(images, [])

        important_precedence = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <image style="display: none ! important; display: inline"
            width="10" height="10" href="{TINY_DATA_URI}"/>
        </svg>'''
        images, _warnings = extract_svg_images(important_precedence)
        self.assertEqual(images, [])

        important_opacity = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <image style="opacity: 25% !important" width="10" height="10" href="{TINY_DATA_URI}"/>
        </svg>'''
        images, _warnings = extract_svg_images(important_opacity)
        self.assertAlmostEqual(images[0]["opacity"], 0.25)

        inherited = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <g opacity="0.5"><image opacity="inherit" width="10" height="10" href="{TINY_DATA_URI}"/></g>
        </svg>'''
        images, _warnings = extract_svg_images(inherited)
        self.assertAlmostEqual(images[0]["opacity"], 0.25)

    def test_inline_style_overrides_image_geometry_attributes(self):
        svg = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
          <image x="1" y="2" width="10" height="10"
            style="x: 20px; y: 30px; width: 40px; height: 50px"
            href="{TINY_DATA_URI}"/>
        </svg>'''
        images, _warnings = extract_svg_images(svg)
        self.assertEqual(
            images[0]["rect"],
            (20.0, 30.0, 40.0, 50.0),
        )

    def test_preprocessed_use_transform_matches_svg_semantics(self):
        raw = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
          <defs><g id="asset"><image width="10" height="10" href="{TINY_DATA_URI}"/></g></defs>
          <use href="#asset" x="10" transform="scale(2)"/>
        </svg>'''
        images, _warnings = extract_svg_images(preprocess_svg(raw))
        self.assertEqual(
            images[0]["corners"],
            [(20.0, 0.0), (40.0, 0.0), (40.0, 20.0), (20.0, 20.0)],
        )

    def test_preprocessed_use_accepts_percentage_and_unit_offsets(self):
        raw = f'''<svg xmlns="{SVG_NS}" width="200" height="100">
          <defs><image id="asset" width="10" height="10" href="{TINY_DATA_URI}"/></defs>
          <use href="#asset" x="10%" y="1cm"/>
        </svg>'''
        images, _warnings = extract_svg_images(preprocess_svg(raw))
        self.assertAlmostEqual(images[0]["corners"][0][0], 20.0)
        self.assertAlmostEqual(images[0]["corners"][0][1], 90.0 / 2.54)

    def test_use_dimensions_do_not_scale_ordinary_graphics_target(self):
        raw = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
          <defs><g id="asset">
            <image width="10" height="20" href="{TINY_DATA_URI}"/>
          </g></defs>
          <use href="#asset" width="40" height="60"/>
        </svg>'''
        images, _warnings = extract_svg_images(preprocess_svg(raw))
        xs = [corner[0] for corner in images[0]["corners"]]
        ys = [corner[1] for corner in images[0]["corners"]]
        self.assertAlmostEqual(max(xs) - min(xs), 10.0)
        self.assertAlmostEqual(max(ys) - min(ys), 20.0)

    def test_preprocessed_symbol_keeps_its_instance_viewport(self):
        raw = f'''<svg xmlns="{SVG_NS}" width="100" height="50">
          <defs><symbol id="asset" viewBox="0 0 10 10">
            <image width="100%" height="100%" href="{TINY_DATA_URI}"/>
          </symbol></defs>
          <use href="#asset" width="100" height="50"/>
        </svg>'''
        images, _warnings = extract_svg_images(preprocess_svg(raw))
        xs = [corner[0] for corner in images[0]["corners"]]
        ys = [corner[1] for corner in images[0]["corners"]]
        self.assertEqual(images[0]["rect"], (0.0, 0.0, 10.0, 10.0))
        self.assertAlmostEqual(max(xs) - min(xs), 50.0)
        self.assertAlmostEqual(max(ys) - min(ys), 50.0)
        self.assertAlmostEqual(min(xs), 25.0)
        self.assertAlmostEqual(min(ys), 0.0)

    def test_preprocessed_symbol_transform_precedes_viewport_matrix(self):
        raw = f'''<svg xmlns="{SVG_NS}" width="100" height="50">
          <defs><symbol id="asset" viewBox="0 0 10 10"
            transform="translate(2 3)">
            <image width="1" height="1" href="{TINY_DATA_URI}"/>
          </symbol></defs>
          <use href="#asset" width="100" height="50"/>
        </svg>'''
        images, _warnings = extract_svg_images(preprocess_svg(raw))
        xs = [corner[0] for corner in images[0]["corners"]]
        ys = [corner[1] for corner in images[0]["corners"]]
        self.assertAlmostEqual(min(xs), 27.0)
        self.assertAlmostEqual(min(ys), 3.0)

    def test_symbol_use_dimensions_do_not_add_parent_viewport_scaling(self):
        raw = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
          <defs><symbol id="asset" viewBox="0 0 10 20">
            <image width="10" height="20" href="{TINY_DATA_URI}"/>
          </symbol></defs>
          <use href="#asset" x="4" y="5" width="40" height="60"/>
        </svg>'''
        images, _warnings = extract_svg_images(preprocess_svg(raw))
        xs = [corner[0] for corner in images[0]["corners"]]
        ys = [corner[1] for corner in images[0]["corners"]]
        self.assertAlmostEqual(min(xs), 9.0)
        self.assertAlmostEqual(max(xs), 39.0)
        self.assertAlmostEqual(min(ys), 5.0)
        self.assertAlmostEqual(max(ys), 65.0)

    def test_use_dimensions_override_referenced_svg_dimensions(self):
        raw = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
          <defs><svg id="asset" width="10" height="20"
            viewBox="0 0 10 20">
            <image width="10" height="20" href="{TINY_DATA_URI}"/>
          </svg></defs>
          <use href="#asset" x="4" y="5" width="40" height="60"/>
        </svg>'''
        images, _warnings = extract_svg_images(preprocess_svg(raw))
        xs = [corner[0] for corner in images[0]["corners"]]
        ys = [corner[1] for corner in images[0]["corners"]]
        self.assertAlmostEqual(min(xs), 9.0)
        self.assertAlmostEqual(max(xs), 39.0)
        self.assertAlmostEqual(min(ys), 5.0)
        self.assertAlmostEqual(max(ys), 65.0)

    def test_referenced_svg_percentage_offsets_use_parent_viewport(self):
        raw = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
          <defs><svg id="asset" x="10%" y="20%" width="50%" height="25%">
            <image width="100%" height="100%" preserveAspectRatio="none"
              href="{TINY_DATA_URI}"/>
          </svg></defs>
          <use href="#asset"/>
        </svg>'''
        images, _warnings = extract_svg_images(preprocess_svg(raw))
        xs = [corner[0] for corner in images[0]["corners"]]
        ys = [corner[1] for corner in images[0]["corners"]]
        self.assertAlmostEqual(min(xs), 10.0)
        self.assertAlmostEqual(max(xs), 60.0)
        self.assertAlmostEqual(min(ys), 20.0)
        self.assertAlmostEqual(max(ys), 45.0)

    def test_symbol_preserve_aspect_ratio_is_baked_for_blender(self):
        none = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
          <defs><symbol id="asset" viewBox="0 0 10 20"
            preserveAspectRatio="none">
            <image width="10" height="20" preserveAspectRatio="none"
              href="{TINY_DATA_URI}"/>
          </symbol></defs>
          <use href="#asset" width="40" height="20"/>
        </svg>'''
        images, _warnings = extract_svg_images(preprocess_svg(none))
        xs = [corner[0] for corner in images[0]["corners"]]
        ys = [corner[1] for corner in images[0]["corners"]]
        self.assertAlmostEqual(min(xs), 0.0)
        self.assertAlmostEqual(max(xs), 40.0)
        self.assertAlmostEqual(min(ys), 0.0)
        self.assertAlmostEqual(max(ys), 20.0)

        aligned = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
          <defs><symbol id="asset" viewBox="0 0 10 20"
            preserveAspectRatio="xMaxYMax meet">
            <image width="10" height="20" preserveAspectRatio="none"
              href="{TINY_DATA_URI}"/>
          </symbol></defs>
          <use href="#asset" x="50" width="40" height="20"/>
        </svg>'''
        images, _warnings = extract_svg_images(preprocess_svg(aligned))
        xs = [corner[0] for corner in images[0]["corners"]]
        ys = [corner[1] for corner in images[0]["corners"]]
        self.assertAlmostEqual(min(xs), 80.0)
        self.assertAlmostEqual(max(xs), 90.0)
        self.assertAlmostEqual(min(ys), 0.0)
        self.assertAlmostEqual(max(ys), 20.0)

    def test_symbol_nonzero_viewbox_origin_corrects_blender_mapping(self):
        expected = {
            "none": (11.0, 51.0, 13.0, 43.0),
            "xMidYMid meet": (23.5, 38.5, 13.0, 43.0),
        }
        for preserve_aspect_ratio, bounds in expected.items():
            with self.subTest(preserve_aspect_ratio=preserve_aspect_ratio):
                raw = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
                  <defs><symbol id="asset" viewBox="5 7 10 20"
                    preserveAspectRatio="{preserve_aspect_ratio}">
                    <image x="5" y="7" width="10" height="20"
                      preserveAspectRatio="none" href="{TINY_DATA_URI}"/>
                  </symbol></defs>
                  <use href="#asset" x="11" y="13" width="40" height="30"/>
                </svg>'''
                images, _warnings = extract_svg_images(preprocess_svg(raw))
                xs = [corner[0] for corner in images[0]["corners"]]
                ys = [corner[1] for corner in images[0]["corners"]]
                self.assertAlmostEqual(min(xs), bounds[0])
                self.assertAlmostEqual(max(xs), bounds[1])
                self.assertAlmostEqual(min(ys), bounds[2])
                self.assertAlmostEqual(max(ys), bounds[3])

    def test_recursive_use_expansion_is_bounded(self):
        raw = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <defs><g id="loop">
            <use href="#loop"/><use href="#loop"/><use href="#loop"/>
          </g></defs>
          <use href="#loop"/>
        </svg>'''
        with self.assertRaisesRegex(ValueError, "expansion limit"):
            preprocess_svg(raw)

    def test_cloned_stroke_conversion_work_is_bounded(self):
        raw = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <defs><g id="loop">
            <path d="M0 0 L1 1" fill="none" stroke="#000"
              stroke-width="1"/>
            <use href="#loop"/><use href="#loop"/>
          </g></defs>
          <use href="#loop"/>
        </svg>'''
        with self.assertRaisesRegex(ValueError, "stroke conversion work limit"):
            preprocess_svg(raw)

    def test_preprocessed_svg_target_keeps_its_offsets(self):
        raw = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
          <defs><svg id="asset" x="2" y="3" width="10" height="10"
            viewBox="0 0 10 10">
            <image width="1" height="1" href="{TINY_DATA_URI}"/>
          </svg></defs>
          <use href="#asset" x="4" y="5"/>
        </svg>'''
        images, _warnings = extract_svg_images(preprocess_svg(raw))
        xs = [corner[0] for corner in images[0]["corners"]]
        self.assertAlmostEqual(min(xs), 6.0)

    def test_nested_use_alias_chain_keeps_image_and_marker(self):
        definitions = [
            f'<image id="asset0" width="1" height="1" href="{TINY_DATA_URI}"/>'
        ]
        for index in range(1, 10):
            definitions.append(
                f'<g id="asset{index}"><use href="#asset{index - 1}"/></g>'
            )
        raw = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <defs>{''.join(definitions)}</defs>
          <use href="#asset9"/>
        </svg>'''
        processed = preprocess_svg(raw)
        images, warnings, _marked_svg, marker_ids = prepare_svg_images(processed)
        self.assertEqual(warnings, [])
        self.assertEqual(len(images), 1)
        self.assertEqual(len(marker_ids), 1)

    def test_repeated_placements_share_decoded_payload(self):
        raw = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
          <defs><image id="asset" width="10" height="10" href="{TINY_DATA_URI}"/></defs>
          <use href="#asset" x="10"/><use href="#asset" x="30"/>
        </svg>'''
        images, _warnings = extract_svg_images(preprocess_svg(raw))
        self.assertEqual(len(images), 2)
        self.assertIs(images[0]["data"], images[1]["data"])

    def test_plain_href_takes_precedence_even_when_empty(self):
        svg = f'''<svg xmlns="{SVG_NS}"
          xmlns:xlink="http://www.w3.org/1999/xlink" width="10" height="10">
          <image width="10" height="10" href="" xlink:href="{TINY_DATA_URI}"/>
        </svg>'''
        images, _warnings = extract_svg_images(svg)
        self.assertEqual(images, [])

    def test_preserve_aspect_ratio_meet_none_and_slice(self):
        info = {
            "rect": (0.0, 0.0, 200.0, 100.0),
            "matrix": (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            "preserve_aspect_ratio": "xMidYMid meet",
        }
        corners, uvs = _placement_geometry(info, (100, 100))
        self.assertEqual(
            corners,
            [(50.0, 0.0), (150.0, 0.0), (150.0, 100.0), (50.0, 100.0)],
        )
        self.assertEqual(uvs, [(0, 1), (1, 1), (1, 0), (0, 0)])

        info["preserve_aspect_ratio"] = "none"
        corners, _uvs = _placement_geometry(info, (100, 100))
        self.assertEqual(
            corners,
            [(0.0, 0.0), (200.0, 0.0), (200.0, 100.0), (0.0, 100.0)],
        )

        info["preserve_aspect_ratio"] = "none slice"
        corners, _uvs = _placement_geometry(info, (100, 100))
        self.assertEqual(
            corners,
            [(0.0, 0.0), (200.0, 0.0), (200.0, 100.0), (0.0, 100.0)],
        )

        info["preserve_aspect_ratio"] = "xMidYMid slice"
        corners, uvs = _placement_geometry(info, (100, 100))
        self.assertEqual(
            corners,
            [(0.0, 0.0), (200.0, 0.0), (200.0, 100.0), (0.0, 100.0)],
        )
        self.assertEqual(uvs, [(0.0, 0.75), (1.0, 0.75), (1.0, 0.25), (0.0, 0.25)])

        info["preserve_aspect_ratio"] = "bogus slice"
        corners, _uvs = _placement_geometry(info, (100, 100))
        self.assertEqual(
            corners,
            [(50.0, 0.0), (150.0, 0.0), (150.0, 100.0), (50.0, 100.0)],
        )

    def test_auto_dimension_uses_intrinsic_ratio(self):
        info = {
            "rect": (0.0, 0.0, None, 50.0),
            "matrix": (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            "preserve_aspect_ratio": "none",
        }
        corners, _uvs = _placement_geometry(info, (200, 100))
        self.assertEqual(corners[2], (100.0, 50.0))

    def test_external_paths_are_contained_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "svg"
            root.mkdir()
            inside = root / "inside.png"
            outside = Path(temp_dir) / "outside.png"
            inside.write_bytes(b"inside")
            outside.write_bytes(b"outside")

            inside_svg = f'''<svg xmlns="{SVG_NS}" width="1" height="1">
              <image width="1" height="1" href="{inside.as_uri()}"/>
            </svg>'''
            images, warnings = extract_svg_images(inside_svg, svg_dir=root)
            self.assertEqual(warnings, [])
            self.assertEqual(images[0]["data"], b"inside")

            outside_svg = f'''<svg xmlns="{SVG_NS}" width="1" height="1">
              <image width="1" height="1" href="../outside.png"/>
            </svg>'''
            images, warnings = extract_svg_images(outside_svg, svg_dir=root)
            self.assertEqual(images, [])
            self.assertTrue(any("Blocked" in warning for warning in warnings))

            images, _warnings = extract_svg_images(
                outside_svg,
                svg_dir=root,
                allow_external_outside_svg=True,
            )
            self.assertEqual(images[0]["data"], b"outside")

    def test_file_uri_with_literal_percent_escape_is_decoded_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_path = root / "literal%20.png"
            image_path.write_bytes(b"literal percent filename")
            svg = f'''<svg xmlns="{SVG_NS}" width="1" height="1">
              <image width="1" height="1" href="{image_path.as_uri()}"/>
            </svg>'''
            images, warnings = extract_svg_images(svg, svg_dir=root)
            self.assertEqual(warnings, [])
            self.assertEqual(images[0]["data"], b"literal percent filename")

    def test_embedded_stylesheet_limitation_is_reported(self):
        svg = f'''<svg xmlns="{SVG_NS}" width="1" height="1">
          <style>.hidden {{ display: none; }}</style>
          <image class="hidden" width="1" height="1" href="{TINY_DATA_URI}"/>
        </svg>'''
        images, warnings = extract_svg_images(svg)
        self.assertEqual(len(images), 1)
        self.assertTrue(any("stylesheets" in warning for warning in warnings))

    def test_preparation_assigns_stable_markers_even_for_bad_images(self):
        svg = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <rect id="before" width="1" height="1"/>
          <image width="1" height="1" href="data:image/png;base64,%%%"/>
          <rect id="after" x="2" width="1" height="1"/>
        </svg>'''
        images, warnings, marked_svg, marker_ids = prepare_svg_images(svg)
        self.assertEqual(images, [])
        self.assertEqual(len(marker_ids), 1)
        self.assertIn(marker_ids[0], marked_svg)
        self.assertNotIn("<image", marked_svg)
        self.assertTrue(any("undecodable" in warning for warning in warnings))


class BlenderIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        enhanced_svg.register()

    @classmethod
    def tearDownClass(cls):
        enhanced_svg.unregister()

    def _import_svg(self, svg, operator):
        before = _snapshot_blender_data()
        temporary = tempfile.NamedTemporaryFile(
            mode="w", suffix=".svg", encoding="utf-8", delete=False
        )
        temporary.write(svg)
        temporary.close()
        try:
            result = operator(filepath=temporary.name)
            self.assertEqual(result, {"FINISHED"})
            new_collections = set(bpy.data.collections) - before["collections"]
            imported = next(
                collection
                for collection in new_collections
                if collection.name.startswith("SVG_")
            )
            return before, imported
        finally:
            Path(temporary.name).unlink(missing_ok=True)

    def test_processed_import_packs_image_and_preserves_paint_order(self):
        uri = _data_uri(2, 1)
        svg = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
          <rect id="behind" width="100" height="100" fill="#00ff00"/>
          <g opacity="0.5"><image id="picture" opacity="0.5" x="10" y="20" width="40" height="40" href="{uri}"/></g>
          <rect id="front" x="20" y="30" width="10" height="10" fill="#0000ff"/>
        </svg>'''
        before, collection = self._import_svg(
            svg, bpy.ops.import_scene.import_svg
        )
        try:
            self.assertEqual(
                [(obj.name, obj.type) for obj in collection.objects],
                [("behind", "CURVE"), ("Image_picture", "MESH"), ("front", "CURVE")],
            )
            self.assertEqual(
                [obj.get("svg_paint_index") for obj in collection.objects],
                [0, 1, 2],
            )
            self.assertEqual(
                [round(obj.location.z, 7) for obj in collection.objects],
                [0.0, PAINT_ORDER_Z_STEP, 2 * PAINT_ORDER_Z_STEP],
            )
            self.assertFalse(
                any(obj.name.startswith("__ESVG_IMG_") for obj in bpy.data.objects)
            )

            plane = collection.objects[1]
            self.assertAlmostEqual(plane["opacity"], 0.25)
            xs = [vertex.co.x for vertex in plane.data.vertices]
            ys = [vertex.co.y for vertex in plane.data.vertices]
            self.assertAlmostEqual(max(xs) - min(xs), 40 * BLENDER_SCALE)
            self.assertAlmostEqual(max(ys) - min(ys), 20 * BLENDER_SCALE)
            texture = next(
                node
                for node in plane.data.materials[0].node_tree.nodes
                if node.bl_idname == "ShaderNodeTexImage"
            )
            self.assertTrue(texture.image.packed_file)
        finally:
            _restore_blender_data(before)

    def test_link_container_preserves_image_paint_order(self):
        uri = _data_uri(1, 1)
        svg = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <rect id="background" width="10" height="10" fill="#00ff00"/>
          <a href="https://example.com">
            <image id="linked" width="5" height="5" href="{uri}"/>
          </a>
          <rect id="foreground" x="5" y="5" width="5" height="5" fill="#0000ff"/>
        </svg>'''
        before, collection = self._import_svg(
            svg, bpy.ops.import_scene.import_svg
        )
        try:
            self.assertEqual(
                [obj.name for obj in collection.objects],
                ["background", "Image_linked", "foreground"],
            )
        finally:
            _restore_blender_data(before)

    def test_graphics_use_dimensions_keep_vector_image_alignment(self):
        uri = _data_uri(1, 1)
        svg = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
          <defs><g id="asset">
            <rect id="vector" width="10" height="20" fill="#00ff00"/>
            <image width="10" height="20" preserveAspectRatio="none"
              href="{uri}"/>
          </g></defs>
          <use href="#asset" width="40" height="60"/>
        </svg>'''
        before, collection = self._import_svg(
            svg, bpy.ops.import_scene.import_svg
        )
        try:
            curve = next(obj for obj in collection.objects if obj.type == "CURVE")
            plane = next(obj for obj in collection.objects if obj.type == "MESH")
            self.assertAlmostEqual(
                curve.dimensions.x, 10 * BLENDER_SCALE, delta=2e-6
            )
            self.assertAlmostEqual(
                curve.dimensions.y, 20 * BLENDER_SCALE, delta=2e-6
            )
            self.assertAlmostEqual(
                plane.dimensions.x, curve.dimensions.x, delta=2e-6
            )
            self.assertAlmostEqual(
                plane.dimensions.y, curve.dimensions.y, delta=2e-6
            )
        finally:
            _restore_blender_data(before)

    def test_symbol_aspect_ratio_keeps_vector_image_alignment(self):
        uri = _data_uri(1, 2)
        svg = f'''<svg xmlns="{SVG_NS}" width="100" height="100">
          <defs><symbol id="asset" viewBox="0 0 10 20"
            preserveAspectRatio="none">
            <rect id="vector" width="10" height="20" fill="#00ff00"/>
            <image width="10" height="20" preserveAspectRatio="none"
              href="{uri}"/>
          </symbol></defs>
          <use href="#asset" width="40" height="20"/>
        </svg>'''
        before, collection = self._import_svg(
            svg, bpy.ops.import_scene.import_svg
        )
        try:
            curve = next(obj for obj in collection.objects if obj.type == "CURVE")
            plane = next(obj for obj in collection.objects if obj.type == "MESH")
            self.assertAlmostEqual(
                curve.dimensions.x, 40 * BLENDER_SCALE, delta=2e-6
            )
            self.assertAlmostEqual(
                curve.dimensions.y, 20 * BLENDER_SCALE, delta=2e-6
            )
            self.assertAlmostEqual(
                plane.dimensions.x, curve.dimensions.x, delta=2e-6
            )
            self.assertAlmostEqual(
                plane.dimensions.y, curve.dimensions.y, delta=2e-6
            )
        finally:
            _restore_blender_data(before)

    def test_unsupported_paint_does_not_break_material_tracking(self):
        svg = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <defs><linearGradient id="gradient"/></defs>
          <rect id="gradient-rect" width="10" height="10"
            fill="url(#gradient)"/>
        </svg>'''
        before, collection = self._import_svg(
            svg, bpy.ops.import_scene.import_svg
        )
        try:
            self.assertEqual([obj.name for obj in collection.objects], ["gradient-rect"])
        finally:
            _restore_blender_data(before)

    def test_image_only_import_does_not_leave_marker_material(self):
        uri = _data_uri(1, 1)
        svg = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <image width="10" height="10" href="{uri}"/>
        </svg>'''
        for operator in (
            bpy.ops.import_scene.import_svg,
            bpy.ops.import_scene.import_svg_emission,
        ):
            before = _snapshot_blender_data()
            temporary = tempfile.NamedTemporaryFile(
                mode="w", suffix=".svg", encoding="utf-8", delete=False
            )
            temporary.write(svg)
            temporary.close()
            try:
                self.assertEqual(operator(filepath=temporary.name), {"FINISHED"})
                unused = [
                    material
                    for material in set(bpy.data.materials) - before["materials"]
                    if material.users == 0
                    and (
                        material.get("enhanced_svg_blender_material")
                        or material.get("enhanced_svg_curve_material")
                    )
                ]
                self.assertEqual(unused, [])
            finally:
                Path(temporary.name).unlink(missing_ok=True)
                _restore_blender_data(before)

    def test_missing_marker_discards_generated_plane(self):
        before = _snapshot_blender_data()
        collection = bpy.data.collections.new("missing_marker_test")
        after_collection = _snapshot_blender_data()
        image_info = {
            "name": "orphan",
            "data": _png_bytes(),
            "ext": ".png",
            "rect": (0.0, 0.0, 1.0, 1.0),
            "matrix": (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            "preserve_aspect_ratio": "none",
            "opacity": 1.0,
            "marker_id": "missing-marker",
        }
        warnings = []
        try:
            created = create_image_planes([image_info], collection)
            self.assertEqual(len(created), 1)
            ordered = finalize_paint_order(
                collection,
                [],
                [image_info],
                ["missing-marker"],
                warnings,
            )
            self.assertEqual(ordered, [])
            self.assertEqual(_snapshot_blender_data(), after_collection)
            self.assertTrue(any("Skipped image" in warning for warning in warnings))
        finally:
            _restore_blender_data(before)

    def test_failed_blender_import_rolls_back_created_data(self):
        before = _snapshot_blender_data()
        uri = _data_uri(1, 1)
        svg = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <image id="before-error" width="1" height="1" href="{uri}"/>
          <rect id="broken" width="bogus" height="1"/>
        </svg>'''
        temporary = tempfile.NamedTemporaryFile(
            mode="w", suffix=".svg", encoding="utf-8", delete=False
        )
        temporary.write(svg)
        temporary.close()
        try:
            with self.assertRaises(RuntimeError):
                bpy.ops.import_scene.import_svg(filepath=temporary.name)
            self.assertEqual(_snapshot_blender_data(), before)
        finally:
            Path(temporary.name).unlink(missing_ok=True)
            _restore_blender_data(before)

    def test_post_import_failure_rolls_back_created_data(self):
        before = _snapshot_blender_data()
        uri = _data_uri(1, 1)
        svg = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <image id="before-error" width="1" height="1" href="{uri}"/>
        </svg>'''
        temporary = tempfile.NamedTemporaryFile(
            mode="w", suffix=".svg", encoding="utf-8", delete=False
        )
        temporary.write(svg)
        temporary.close()
        original_create_image_planes = imports_module.create_image_planes
        sentinel = {}

        def raise_after_import(*_args, **_kwargs):
            mesh = bpy.data.meshes.new("unrelated_handler_mesh")
            obj = bpy.data.objects.new("unrelated_handler_object", mesh)
            bpy.context.scene.collection.objects.link(obj)
            collection = bpy.data.collections.new(
                "unrelated_handler_collection"
            )
            bpy.context.scene.collection.children.link(collection)
            collection.objects.link(obj)
            # This deliberately collides with Blender's own SVG material
            # naming; rollback ownership must not be inferred from the name.
            material = bpy.data.materials.new("SVGMat.004")
            sentinel.update(
                {
                    "collection": collection,
                    "mesh": mesh,
                    "object": obj,
                    "material": material,
                }
            )
            raise RuntimeError("forced post-import failure")

        imports_module.create_image_planes = raise_after_import
        try:
            with self.assertRaises(RuntimeError):
                bpy.ops.import_scene.import_svg(filepath=temporary.name)
            after = _snapshot_blender_data()
            self.assertEqual(
                after["objects"] - before["objects"],
                {sentinel["object"]},
            )
            self.assertEqual(
                after["meshes"] - before["meshes"],
                {sentinel["mesh"]},
            )
            self.assertEqual(
                after["materials"] - before["materials"],
                {sentinel["material"]},
            )
            self.assertEqual(
                after["collections"] - before["collections"],
                {sentinel["collection"]},
            )
            for kind in ("curves", "images"):
                self.assertEqual(after[kind], before[kind])
        finally:
            imports_module.create_image_planes = original_create_image_planes
            Path(temporary.name).unlink(missing_ok=True)
            _restore_blender_data(before)

    def test_failed_import_preserves_preexisting_child_collection(self):
        initial = _snapshot_blender_data()
        user_collection = bpy.data.collections.new("preexisting_user_collection")
        bpy.context.scene.collection.children.link(user_collection)
        user_mesh = bpy.data.meshes.new("preexisting_user_mesh")
        user_object = bpy.data.objects.new("preexisting_user_object", user_mesh)
        user_collection.objects.link(user_object)
        before = _snapshot_blender_data()
        uri = _data_uri(1, 1)
        svg = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <image id="before-error" width="1" height="1" href="{uri}"/>
        </svg>'''
        temporary = tempfile.NamedTemporaryFile(
            mode="w", suffix=".svg", encoding="utf-8", delete=False
        )
        temporary.write(svg)
        temporary.close()
        original_create_image_planes = imports_module.create_image_planes

        def link_existing_child_then_raise(_images, imported_collection, **_kwargs):
            imported_collection.children.link(user_collection)
            raise RuntimeError("forced post-import failure")

        imports_module.create_image_planes = link_existing_child_then_raise
        try:
            with self.assertRaises(RuntimeError):
                bpy.ops.import_scene.import_svg(filepath=temporary.name)
            self.assertEqual(_snapshot_blender_data(), before)
            self.assertIn(user_collection, bpy.context.scene.collection.children[:])
            self.assertIn(user_object, user_collection.objects[:])
        finally:
            imports_module.create_image_planes = original_create_image_planes
            Path(temporary.name).unlink(missing_ok=True)
            _restore_blender_data(initial)

    def test_emission_import_does_not_reuse_unrelated_material(self):
        before = _snapshot_blender_data()
        dummy = bpy.data.materials.new("MatImg_picture")
        uri = _data_uri(1, 1)
        svg = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <image id="picture" width="10" height="10" href="{uri}"/>
        </svg>'''
        temporary = tempfile.NamedTemporaryFile(
            mode="w", suffix=".svg", encoding="utf-8", delete=False
        )
        temporary.write(svg)
        temporary.close()
        try:
            result = bpy.ops.import_scene.import_svg_emission(filepath=temporary.name)
            self.assertEqual(result, {"FINISHED"})
            collection = next(
                collection
                for collection in set(bpy.data.collections) - before["collections"]
                if collection.name.startswith("SVG_Emission")
            )
            plane = next(obj for obj in collection.objects if obj.type == "MESH")
            material = plane.data.materials[0]
            self.assertIsNot(material, dummy)
            self.assertTrue(material["enhanced_svg_image_material"])
            self.assertTrue(
                any(
                    node.bl_idname == "ShaderNodeEmission"
                    for node in material.node_tree.nodes
                )
            )
        finally:
            Path(temporary.name).unlink(missing_ok=True)
            _restore_blender_data(before)

    def test_emission_curve_does_not_reuse_name_collision(self):
        before = _snapshot_blender_data()
        dummy = bpy.data.materials.new("Mat0_#ff0000")
        svg = f'''<svg xmlns="{SVG_NS}" width="10" height="10">
          <rect id="red" width="10" height="10" fill="#ff0000"/>
        </svg>'''
        temporary = tempfile.NamedTemporaryFile(
            mode="w", suffix=".svg", encoding="utf-8", delete=False
        )
        temporary.write(svg)
        temporary.close()
        try:
            result = bpy.ops.import_scene.import_svg_emission(
                filepath=temporary.name
            )
            self.assertEqual(result, {"FINISHED"})
            collection = next(
                collection
                for collection in set(bpy.data.collections) - before["collections"]
                if collection.name.startswith("SVG_Emission")
            )
            material = collection.objects[0].data.materials[0]
            self.assertIsNot(material, dummy)
            self.assertTrue(material["enhanced_svg_curve_material"])
        finally:
            Path(temporary.name).unlink(missing_ok=True)
            _restore_blender_data(before)

    def test_empty_material_slot_is_ignored(self):
        before = _snapshot_blender_data()
        collection = bpy.data.collections.new("empty_material_test")
        mesh = bpy.data.meshes.new("empty_material_mesh")
        obj = bpy.data.objects.new("empty_material_object", mesh)
        collection.objects.link(obj)
        mesh.materials.append(None)
        try:
            deduplicate_materials(collection)
        finally:
            _restore_blender_data(before)

    def test_import_collection_selection_ignores_companion_collection(self):
        before = _snapshot_blender_data()
        imported = bpy.data.collections.new("temporary.svg")
        companion = bpy.data.collections.new("temporary.svg.handler")
        try:
            self.assertIs(
                _select_import_collection(
                    [companion, imported], "temporary.svg"
                ),
                imported,
            )
        finally:
            _restore_blender_data(before)


if __name__ == "__main__":
    unittest.main()
