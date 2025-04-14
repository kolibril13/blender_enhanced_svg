import bpy
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty
from pathlib import Path
import time

from .svg_preprocessing import preprocess_svg

# Operator for the button and drag-and-drop
class ImportSVGOperator(bpy.types.Operator, ImportHelper):
    """Operator to import a .svg file and import as SVG in Blender."""

    bl_idname = "import_scene.import_svg"
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

        # Compile and import the file using our helper function
        processed_svg = preprocess_svg(svg_file)

        collection = bpy.context.collection.children[-1]

        elapsed_time_ms = (time.perf_counter() - start_time) * 1000
        self.report(
            {"INFO"},
            f" 🦢  SVG Importer: {svg_file.name} rendered in {elapsed_time_ms:.2f} ms as {collection.name}",
        )
        return {"FINISHED"}

    def invoke(self, context, event):
        # If the operator was invoked with a filepath (drag–n–drop), execute directly.
        if self.filepath:
            return self.execute(context)
        # Otherwise, open the file browser.
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


# File Handler for drag-and-drop support
class SVG_FH_import(bpy.types.FileHandler):
    """A file handler to allow .svg files to be dragged and dropped directly into Blender."""

    bl_idname = "SVG_FH_import"
    bl_label = "File handler for SVG import"
    bl_import_operator = "import_scene.import_svg"
    bl_file_extensions = ".svg"

    @classmethod
    def poll_drop(cls, context):
        # Allow drag–n–drop
        return context.area is not None
