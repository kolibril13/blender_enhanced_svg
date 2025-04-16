# # this is not yet used, but might be useful for the future
# https://imgur.com/a/TUDphuL

# import os
# from fontTools.ttLib import TTFont
# from fontTools.pens.svgPathPen import SVGPathPen
# from lxml import etree

# def text_to_svg_group(text, font_path, font_size, start_x):
#     """
#     Creates an SVG <g> element with one <path> per glyph.
    
#     Args:
#         text (str): The text to convert.
#         font_path (str): Path to a TTF/OTF font file.
#         font_size (float): Desired output font size (in user units).
#         start_x (float): The initial horizontal offset (in user units).
    
#     Returns:
#         lxml.etree.Element: A <g> element containing the glyph paths.
#     """
#     font = TTFont(font_path)
#     glyph_set = font.getGlyphSet()
#     # Get the mapping from Unicode code points to glyph names.
#     cmap = font['cmap'].getBestCmap()
#     units_per_em = font['head'].unitsPerEm
#     # Compute scaling factor to convert from font units to desired size.
#     scale = font_size / units_per_em
    
#     group = etree.Element("{http://www.w3.org/2000/svg}g")
#     x_cursor = start_x  # in user coordinates
#     for char in text:
#         code_point = ord(char)
#         if code_point not in cmap:
#             continue  # Skip characters not found in the font's cmap.
#         glyph_name = cmap[code_point]
#         glyph = glyph_set[glyph_name]
        
#         pen = SVGPathPen(glyph_set)
#         glyph.draw(pen)
#         glyph_path = pen.getCommands()
        
#         # Create a path element for the current glyph.
#         path_elem = etree.Element("{http://www.w3.org/2000/svg}path")
#         path_elem.set("d", glyph_path)
#         # Set a transform that first scales the raw glyph path and then translates it.
#         # With this transform, the raw coordinates (in font units) are scaled by 'scale'
#         # and then shifted horizontally by x_cursor.
#         path_elem.set("transform", f"translate({x_cursor},0) scale({scale},-{scale})")
#         group.append(path_elem)
        
#         # Advance x_cursor using the glyph's advance width (converted to user units).
#         advance_width, _ = font['hmtx'].metrics[glyph_name]
#         x_cursor += advance_width * scale
    
#     return group

# def convert_text_to_paths_in_svg(svg_content):
#     """
#     Converts all <text> elements in the SVG to groups of <path> elements
#     by replacing them with equivalent glyph outlines.
    
#     Args:
#         svg_content (str): The SVG content as a string.
    
#     Returns:
#         str: The modified SVG content with <text> elements converted to paths.
#     """
#     SVG_NS = "http://www.w3.org/2000/svg"
#     XLINK_NS = "http://www.w3.org/1999/xlink"
#     NS_MAP = {"svg": SVG_NS, "xlink": XLINK_NS}
    
#     # Parse the SVG content.
#     try:
#         root = etree.fromstring(svg_content)
#     except etree.XMLSyntaxError:
#         parser = etree.XMLParser(remove_blank_text=True)
#         root = etree.fromstring(svg_content, parser)
    
#     # Find all <text> elements.
#     text_elements = root.xpath(".//svg:text", namespaces=NS_MAP)
    
#     for text_elem in text_elements:
#         # Create a new group to hold the glyph paths.
#         new_group = etree.Element(f"{{{SVG_NS}}}g")
        
#         # Copy style attributes (like text-anchor, fill, etc.) to the group.
#         for attr_name, attr_value in text_elem.attrib.items():
#             if attr_name not in ("x", "y"):  # x and y will be handled with transforms.
#                 new_group.set(attr_name, attr_value)
        
#         # Get the text content and convert it.
#         text_content = text_elem.text.strip() if text_elem.text else ""
#         x = float(text_elem.get("x", "0"))
#         y = float(text_elem.get("y", "0"))
#         font_size = float(text_elem.get("font-size", "12"))
        
#         # Extract transform if it exists
#         transform = text_elem.get("transform", "")
        
#         # Resolve the font path.
#         try:
#             import matplotlib.font_manager as fm
#             font_family = text_elem.get("font-family", "DejaVu Sans")
#             # Remove quotes and select the first listed font.
#             font_family = font_family.replace('"', '').replace("'", '').split(',')[0].strip()
#             font_path = fm.findfont(font_family)
#         except (ImportError, Exception):
#             # Fallback: Use DejaVu Sans if matplotlib is not available.
#             raise RuntimeError("Matplotlib is required to resolve a default font.")
        
#         # Get the group containing glyph paths.
#         glyph_group = text_to_svg_group(text_content, font_path, font_size, start_x=0)
        
#         # Wrap the glyph group in an outer group to apply the vertical (y) translation.
#         outer_group = etree.Element(f"{{{SVG_NS}}}g")
        
#         # Apply the original transform if it exists, otherwise just use x,y translation
#         if transform:
#             outer_group.set("transform", transform)
#             # If transform already exists, we need to add the glyph group with its own transform
#             # to position it correctly relative to the original text position
#             glyph_group.set("transform", f"translate({x},{y})")
#         else:
#             outer_group.set("transform", f"translate({x},{y})")
        
#         outer_group.append(glyph_group)
        
#         # Replace the original <text> element with the new outer group.
#         parent = text_elem.getparent()
#         if parent is not None:
#             parent.replace(text_elem, outer_group)
    
#     return etree.tostring(root, encoding="unicode", pretty_print=True)