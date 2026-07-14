# Blender Enhanced SVG

Import more complex svgs to Blender as well!

![image](https://github.com/user-attachments/assets/db5908f0-f0ac-4f2c-9915-c75cc7ed13a9)

Left: without preprocessing
Right: with preprocessing
![image](https://github.com/user-attachments/assets/e1ef9646-9f1b-4739-b220-5fae56983d09)

## Embedded raster images

The **SVG (Processed)** and **SVG (Processed + Emission)** importers turn
rendered SVG `<image>` elements into UV-mapped planes. This includes inline
data URIs, images referenced through `<defs>`/`<use>`, and local image files.

- Textures are packed into the current `.blend`. No additional `.blend` file
  is created, and temporary decoder files are deleted after import.
- Image planes retain SVG transforms, percentage dimensions, intrinsic aspect
  ratio, inline visibility/opacity, and painter order relative to curves.
- Repeated placements share one packed Blender image and material.
- Relative references and `file:` URIs inside the SVG folder are allowed by
  default. Enable **Allow Images Outside SVG Folder** only for trusted SVGs.
- Referenced SVG/symbol viewports preserve percentage offsets and
  `preserveAspectRatio` scaling/alignment; excessively expanding recursive
  `<use>` graphs are rejected during preprocessing.
- Blender determines which raster formats it can decode. PNG, JPEG, GIF,
  WebP, BMP, TIFF, and AVIF data URI suffixes are recognized.

The **SVG (Simple)** importer intentionally remains a direct wrapper around
Blender's built-in SVG importer and does not create image planes. Embedded CSS
stylesheets are not evaluated for image visibility or opacity. Group opacity is
approximated per image plane, so overlapping translucent siblings can composite
differently from an SVG renderer. Clipping, masking, and SVG filters on images
are not yet reproduced; this also means `preserveAspectRatio="slice"` content
is scaled/aligned but is not clipped to a referenced symbol/SVG viewport. The
importer emits a warning when explicit clipping features are encountered.

Try the self-contained [`examples/embedded_images.svg`](examples/embedded_images.svg)
file to see a packed image between background and foreground vector objects.

## Development

Run the regression suite in Blender 5.1 or newer:

```sh
blender --background --factory-startup --python-exit-code 1 --python tests/run.py
```



# Changelog

Unreleased

* Import raster images embedded in the SVG as packed, aspect-correct textured
  planes with painter-order, viewport, transform, visibility, and opacity
  handling. Local external references are contained to the SVG folder unless
  explicitly allowed.
* Fix crash in the emission importer when a curve has an empty material slot

v0.2.0

Support for Blender 5.1

v0.1.8

* Add z offset panel
![alt text](image.png)


v0.1.7

* another try

v0.1.3

* Bump versions of dependencies
* automate workflow

v0.1.2

* Fix import error: https://github.com/kolibril13/blender_enhanced_svg/issues/1#issuecomment-3071963714
