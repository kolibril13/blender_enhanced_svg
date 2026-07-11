from lxml import etree
import copy
import math
from svg.path import parse_path

# SVG namespace used throughout.
SVG_NS = "http://www.w3.org/2000/svg"
NS_MAP = {"svg": SVG_NS, "xlink": "http://www.w3.org/1999/xlink"}


def _ensure_unicode(xml_string):
    """
    Ensures the input XML string is a Unicode string without an XML encoding declaration.
    If the input is bytes, decodes as UTF-8.
    If the input is str, strips any XML encoding declaration.
    """
    if isinstance(xml_string, bytes):
        xml_string = xml_string.decode("utf-8")
    # Remove XML encoding declaration if present
    import re
    xml_string = re.sub(r'<\?xml[^>]*encoding=[\'"].*?[\'"][^>]*\?>', '', xml_string, flags=re.IGNORECASE)
    return xml_string


def flatten_svg(svg_content):
    """
    Replaces all <use xlink:href="#..."> references with the actual symbol contents,
    preserving transforms and styles so the final visual layout is unchanged.
    """
    svg_content = _ensure_unicode(svg_content)
    # Parse the SVG content, handling XML declarations if present
    try:
        # First try parsing as a direct XML fragment
        tree = etree.fromstring(svg_content)
    except etree.XMLSyntaxError:
        try:
            # Try parsing as an XML document with potential XML declaration
            parser = etree.XMLParser(remove_blank_text=True)
            tree = etree.fromstring(svg_content, parser)
        except:
            # If still failing, try to handle SVG with doctype or other preamble
            # by extracting just the SVG element
            import re
            svg_match = re.search(r'<svg[^>]*>.*</svg>', svg_content, re.DOTALL)
            if svg_match:
                parser = etree.XMLParser(remove_blank_text=True)
                tree = etree.fromstring(svg_match.group(0), parser)
            else:
                raise

    # Collect every element that can be referenced by ID; <use> may point at
    # any element with an id, not only <symbol> inside <defs>.
    elements_by_id = {}
    for el in tree.xpath("//*[@id]"):
        elements_by_id[el.get("id")] = el

    # Replace each <use> element with a group containing a clone of its
    # referenced element. Repeat to resolve <use> references nested inside
    # cloned content (bounded to guard against reference cycles).
    for _ in range(10):
        use_elements = tree.xpath("//svg:use", namespaces=NS_MAP)
        if not use_elements:
            break
        for use_el in use_elements:
            # SVG 1.1 uses xlink:href, SVG 2 uses plain href.
            href = use_el.get(f"{{{NS_MAP['xlink']}}}href") or use_el.get("href")
            if not (href and href.startswith("#")):
                continue
            target = elements_by_id.get(href[1:])
            if target is None:
                continue

            # Create a new group (<g>) to hold the cloned content.
            new_g = etree.Element(f"{{{SVG_NS}}}g")

            # Incorporate any x, y, and transform attributes.
            x = float(use_el.get("x", "0"))
            y = float(use_el.get("y", "0"))
            transform = use_el.get("transform", "")
            transforms = []
            if x != 0 or y != 0:
                transforms.append(f"translate({x},{y})")
            if transform:
                transforms.append(transform)
            if transforms:
                new_g.set("transform", " ".join(transforms))

            # Copy over any additional attributes (such as fill, etc.).
            for attr_name, attr_value in use_el.items():
                if attr_name not in (
                    "x",
                    "y",
                    "transform",
                    "href",
                    f"{{{NS_MAP['xlink']}}}href",
                ):
                    new_g.set(attr_name, attr_value)

            # <symbol> and <svg> targets contribute their children; any other
            # element is cloned as a whole.
            local_name = etree.QName(target).localname
            if local_name in ("symbol", "svg"):
                for child in target:
                    new_g.append(copy.deepcopy(child))
            else:
                clone = copy.deepcopy(target)
                # Drop the id so the flattened output has no duplicate ids.
                clone.attrib.pop("id", None)
                new_g.append(clone)

            # Replace the <use> element with the new group.
            parent = use_el.getparent()
            if parent is not None:
                parent.replace(use_el, new_g)

    # Remove the entire <defs> section (no longer needed).
    for defs in tree.xpath("//svg:defs", namespaces=NS_MAP):
        parent = defs.getparent()
        if parent is not None:
            parent.remove(defs)

    # (Optional) Clean up root <svg> attributes.
    allowed_attribs = {
        "viewBox",
        "width",
        "height",
        "xmlns",
        "version",
        "class",
        "style",
        "preserveAspectRatio",
        "baseProfile",
        f"{{{NS_MAP['xlink']}}}xmlns",
    }
    for attr in list(tree.attrib.keys()):
        if attr not in allowed_attribs:
            del tree.attrib[attr]

    return etree.tostring(tree, encoding="unicode", pretty_print=True)


def get_derivative(path_obj, t, dt=1e-6):
    """
    Compute the derivative of the path at parameter t using a finite difference.
    t is clamped between 0 and 1.
    """
    t0 = max(0, t - dt)
    t1 = min(1, t + dt)
    pt0 = path_obj.point(t0)
    pt1 = path_obj.point(t1)
    if t1 - t0 == 0:
        return complex(0, 0)
    return (pt1 - pt0) / (t1 - t0)


def stroke_to_path(d_attr, stroke_width, num_samples=1000):
    """
    Given a path data string (d_attr) and a stroke width, compute an outline
    representing the painted stroke.

    This function samples points along the path, calculates a normal at each point
    (using a finite difference derivative), and builds a closed polygon that follows
    the left side (offset positively) and the right side (offset negatively) of the path.
    """
    path_obj = parse_path(d_attr)
    offset = stroke_width / 2.0

    left_points = []
    right_points = []

    # Sample along the path.
    for i in range(num_samples + 1):
        t = i / num_samples
        pt = path_obj.point(t)
        dpt = get_derivative(path_obj, t)
        dx, dy = dpt.real, dpt.imag
        length = math.hypot(dx, dy)
        if length == 0:
            # Use previous normal as a crude fallback.
            normal = (
                (
                    (left_points[-1][0] - pt.real) / offset,
                    (left_points[-1][1] - pt.imag) / offset,
                )
                if left_points
                else (0, 0)
            )
        else:
            nx, ny = -dy / length, dx / length
            normal = (nx, ny)
        left_pt = (pt.real + normal[0] * offset, pt.imag + normal[1] * offset)
        right_pt = (pt.real - normal[0] * offset, pt.imag - normal[1] * offset)
        left_points.append(left_pt)
        right_points.append(right_pt)

    # Construct the outline path data.
    d_parts = ["M {} {}".format(*left_points[0])]
    d_parts.extend("L {} {}".format(*pt) for pt in left_points[1:])
    d_parts.extend("L {} {}".format(*pt) for pt in reversed(right_points))
    d_parts.append("Z")
    return " ".join(d_parts)


def stroke_to_filled_path(svg_content):
    """
    Parses the SVG content (as a string), finds any <path> elements that use a stroke,
    converts each stroke to a filled outline path, and returns the modified SVG as a string.
    """
    svg_content = _ensure_unicode(svg_content)
    # Parse using lxml, handling XML declarations if present
    try:
        root = etree.fromstring(svg_content)
    except etree.XMLSyntaxError:
        try:
            # Try parsing as an XML document with potential XML declaration
            parser = etree.XMLParser(remove_blank_text=True)
            root = etree.fromstring(svg_content, parser)
        except:
            # If still failing, try to handle SVG with doctype or other preamble
            import re
            svg_match = re.search(r'<svg[^>]*>.*</svg>', svg_content, re.DOTALL)
            if svg_match:
                parser = etree.XMLParser(remove_blank_text=True)
                root = etree.fromstring(svg_match.group(0), parser)
            else:
                raise

    # Find all <path> elements (using XPath with our namespace map).
    path_elems = root.xpath(".//svg:path", namespaces=NS_MAP)
    for path_elem in path_elems:
        attrib = path_elem.attrib
        stroke = attrib.get("stroke")
        if stroke is None or stroke.strip().lower() == "none":
            continue
        if "stroke-width" not in attrib:
            continue
        d_attr = attrib.get("d")
        if not d_attr:
            continue
        try:
            stroke_width = float(attrib.get("stroke-width"))
        except ValueError:
            continue
        if stroke_width <= 0:
            continue

        # Convert the stroke to a filled outline.
        new_d = stroke_to_path(d_attr, stroke_width)

        # Create a new <path> element with the computed outline.
        new_path = etree.Element(f"{{{SVG_NS}}}path")
        new_path.set("d", new_d)
        new_path.set("fill", stroke)
        new_path.set("fill-rule", "nonzero")
        if "transform" in attrib:
            new_path.set("transform", attrib.get("transform"))

        parent = path_elem.getparent()
        if parent is None:
            continue

        fill = attrib.get("fill")
        if fill is not None and fill.strip().lower() == "none":
            # Stroke-only path: the outline fully replaces it.
            parent.replace(path_elem, new_path)
        else:
            # The path also has a visible fill (explicit, or the SVG default
            # black when no fill attribute is set): keep the filled path and
            # paint the stroke outline on top of it.
            for stroke_attr in (
                "stroke",
                "stroke-width",
                "stroke-linecap",
                "stroke-linejoin",
                "stroke-opacity",
                "stroke-dasharray",
                "stroke-dashoffset",
                "stroke-miterlimit",
            ):
                attrib.pop(stroke_attr, None)
            parent.insert(parent.index(path_elem) + 1, new_path)

    return etree.tostring(root, encoding="unicode", pretty_print=True)


# def convert_text_to_paths(svg_content):
#     """
#     Converts all text elements in the SVG to path elements.
#     Uses the text_to_path module to convert text to actual glyph outlines.
#     """
#     from text_to_path import convert_text_to_paths_in_svg
#     return convert_text_to_paths_in_svg(svg_content)


def preprocess_svg(svg_content):
    """
    Performs a three-step preprocessing on the SVG content:
      1. Flattens the SVG by inlining symbols (via flatten_svg).
      2. Converts text elements to path elements (via convert_text_to_paths).
      3. Converts stroked paths into filled outline paths (via stroke_to_filled_path).

    Returns the fully processed SVG content as a string.
    """
    svg_processed = flatten_svg(svg_content)
    # svg_processed = convert_text_to_paths(svg_processed) # not yet ready for use
    svg_processed = stroke_to_filled_path(svg_processed)
    return svg_processed
