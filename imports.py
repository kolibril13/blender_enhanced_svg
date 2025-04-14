import bpy
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty
from pathlib import Path
import tempfile

import time

from .svg_preprocessing import preprocess_svg

# Operator for the button and drag-and-drop with post-processing
class ImportSVGOperator(bpy.types.Operator, ImportHelper):
    """Operator to import a .svg file with post-processing (flattening and stroke conversion)."""

    bl_idname = "import_scene.import_svg"
    bl_label = "Import SVG File with Processing (.svg)"
    bl_options = {"PRESET", "UNDO"}

    # ImportHelper provides a default 'filepath' property,
    # but we redefine it here with SKIP_SAVE to support drag–n–drop.
    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})

    # Set a default extension (the user can change it in the file browser)
    filename_ext = ".svg"
    filter_glob: StringProperty(default="*.svg", options={"HIDDEN"}, maxlen=255)

    def execute(self, context):
        # Verify that the selected file is a .svg file.
        if not self.filepath.lower().endswith(".svg"):
            self.report({"WARNING"}, "Selected file is not an SVG file")
            return {"CANCELLED"}

        # Prepare file variables
        raw_svg_file = Path(self.filepath)
        file_name_without_ext = raw_svg_file.stem

        # Start the timer
        start_time = time.perf_counter()

        # Compile and import the file using our helper function
        processed_svg = preprocess_svg(raw_svg_file.read_text())

        # Create temporary files
        temp_dir = Path(tempfile.gettempdir())
        processed_svg_file = temp_dir / f"{file_name_without_ext}.svg"
        processed_svg_file.write_text(processed_svg)

        bpy.ops.import_curve.svg(filepath=str(processed_svg_file))

        # Get and rename the imported collection
        imported_collection = bpy.context.scene.collection.children.get(
            processed_svg_file.name
        )
        if not imported_collection:
            raise RuntimeError("Failed to import SVG file")

        imported_collection.name = f"SVG_Processed_{file_name_without_ext}"
        imported_collection.processed_svg = processed_svg

        elapsed_time_ms = (time.perf_counter() - start_time) * 1000
        self.report(
            {"INFO"},
            f" 🦢  SVG Importer (Processed): {raw_svg_file.name} rendered in {elapsed_time_ms:.2f} ms as {imported_collection.name}",
        )
        return {"FINISHED"}

    def invoke(self, context, event):
        # If the operator was invoked with a filepath (drag–n–drop), execute directly.
        if self.filepath:
            return self.execute(context)
        # Otherwise, open the file browser.
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


# Operator for simple SVG import without post-processing
class ImportSimpleSVGOperator(bpy.types.Operator, ImportHelper):
    """Operator to import a .svg file without post-processing."""

    bl_idname = "import_scene.import_simple_svg"
    bl_label = "Import SVG File (.svg)"
    bl_options = {"PRESET", "UNDO"}

    # ImportHelper provides a default 'filepath' property,
    # but we redefine it here with SKIP_SAVE to support drag–n–drop.
    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})

    # Set a default extension (the user can change it in the file browser)
    filename_ext = ".svg"
    filter_glob: StringProperty(default="*.svg", options={"HIDDEN"}, maxlen=255)

    def execute(self, context):
        # Verify that the selected file is a .svg file.
        if not self.filepath.lower().endswith(".svg"):
            self.report({"WARNING"}, "Selected file is not an SVG file")
            return {"CANCELLED"}

        # Prepare file variables
        svg_file = Path(self.filepath)
        file_name_without_ext = svg_file.stem

        # Start the timer
        start_time = time.perf_counter()

        # Import SVG directly without preprocessing
        bpy.ops.import_curve.svg(filepath=str(svg_file))

        # Get and rename the imported collection (using the original filename)
        imported_collection = bpy.context.scene.collection.children.get(
            svg_file.name
        )
        if not imported_collection:
            raise RuntimeError("Failed to import SVG file")

        imported_collection.name = f"SVG_Simple_{file_name_without_ext}"

        elapsed_time_ms = (time.perf_counter() - start_time) * 1000
        self.report(
            {"INFO"},
            f" 🦢  SVG Importer (Simple): {svg_file.name} rendered in {elapsed_time_ms:.2f} ms as {imported_collection.name}",
        )
        return {"FINISHED"}

    def invoke(self, context, event):
        # If the operator was invoked with a filepath (drag–n–drop), execute directly.
        if self.filepath:
            return self.execute(context)
        # Otherwise, open the file browser.
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


# File Handler for drag-and-drop support (processed SVG)
class SVG_FH_import(bpy.types.FileHandler):
    """A file handler to allow .svg files to be dragged and dropped directly into Blender with processing."""

    bl_idname = "SVG_FH_import"
    bl_label = "File handler for SVG import with processing"
    bl_import_operator = "import_scene.import_svg"
    bl_file_extensions = ".svg"

    @classmethod
    def poll_drop(cls, context):
        # Allow drag–n–drop
        return context.area is not None

# File Handler for drag-and-drop support (simple SVG)
class SimpleSVG_FH_import(bpy.types.FileHandler):
    """A file handler to allow .svg files to be dragged and dropped directly into Blender without processing."""

    bl_idname = "SimpleSVG_FH_import"
    bl_label = "File handler for simple SVG import"
    bl_import_operator = "import_scene.import_simple_svg"
    bl_file_extensions = ".svg"

    @classmethod
    def poll_drop(cls, context):
        # Allow drag–n–drop 
        return context.area is not None
