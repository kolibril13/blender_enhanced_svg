import bpy
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty
from pathlib import Path
import tempfile
from mathutils import Matrix
import contextlib

import time

from .svg_preprocessing import preprocess_svg

def deduplicate_materials(collection: bpy.types.Collection) -> None:
    """
    Deduplicate materials in a collection by reusing identical materials and giving them descriptive names.

    Args:
        collection: The collection containing objects whose materials need deduplication
    """

    # # Clean up any remaining unused materials that might have been created before
    # for _ in range(3):  # Run multiple times to ensure all orphaned data is removed
    #     bpy.ops.outliner.orphans_purge(do_recursive=True) #TODO : not very tested, and might delete some materials unintended

    materials_dict = {}

    for obj in collection.objects:
        if not obj.data.materials:
            continue

        current_mat = obj.data.materials[0]
        mat_key = tuple(current_mat.diffuse_color)

        if mat_key in materials_dict:
            obj.data.materials.clear()
            obj.data.materials.append(materials_dict[mat_key])
        else:
            rgb = current_mat.diffuse_color[:3]
            hex_color = "".join(f"{int(c*255):02x}" for c in rgb)
            mat_name = f"Mat{len(materials_dict)}_#{hex_color}"

            # Check if material already exists in Blender
            existing_mat = bpy.data.materials.get(mat_name)
            if existing_mat:
                new_mat = existing_mat
            else:
                new_mat = create_material(current_mat.diffuse_color, mat_name)

            materials_dict[mat_key] = new_mat

            obj.data.materials.clear()
            obj.data.materials.append(new_mat)

            if current_mat.users == 0:
                bpy.data.materials.remove(current_mat)

    # Clean up any remaining unused materials
    # for _ in range(3):  # Run multiple times to ensure all orphaned data is removed
    with contextlib.redirect_stdout(None):

        bpy.ops.outliner.orphans_purge(
            do_recursive=True
        )  # TODO : not very tested, and might delete some materials unintended


# Core object and material setup functions
def setup_object(obj: bpy.types.Object, scale_factor: float = 200) -> None:
    """Setup individual object properties."""
    obj.data.transform(Matrix.Scale(scale_factor, 4))
    obj["opacity"] = 1.0
    obj.id_properties_ui("opacity").update(min=0.0, max=1.0, step=0.1)


def create_material(color, name: str = "") -> bpy.types.Material:
    """Create a new material with nodes setup for opacity."""
    # Check if material with this name already exists
    existing_mat = bpy.data.materials.get(name)
    if existing_mat:
        return existing_mat

    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    mat.blend_method = "BLEND"

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    nodes.clear()

    # Create necessary nodes
    transparent = nodes.new(type="ShaderNodeBsdfTransparent")
    emission = nodes.new(type="ShaderNodeEmission")
    mix_shader = nodes.new(type="ShaderNodeMixShader")
    output = nodes.new(type="ShaderNodeOutputMaterial")

    attr_node = nodes.new("ShaderNodeAttribute")
    attr_node.attribute_name = "opacity"
    attr_node.attribute_type = "OBJECT"

    # Set node positions
    attr_node.location = (-300, 300)
    transparent.location = (-300, 100)

    emission.location = (-300, 0)
    mix_shader.location = (0, 100)
    output.location = (300, 100)

    # Set Emission color
    emission.inputs[0].default_value = color  # Use the provided color
    emission.inputs[1].default_value = 1.0  # Emission strength

    # Link nodes
    links.new(transparent.outputs[0], mix_shader.inputs[1])
    links.new(emission.outputs[0], mix_shader.inputs[2])
    links.new(mix_shader.outputs[0], output.inputs[0])
    links.new(
        attr_node.outputs["Fac"], mix_shader.inputs[0]
    )  # Use object opacity attribute

    return mat


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


# Operator for the button and drag-and-drop with post-processing and emission
class ImportSVGEmissionOperator(bpy.types.Operator, ImportHelper):
    """Operator to import a .svg file with post-processing (flattening, stroke conversion) and emission materials."""

    bl_idname = "import_scene.import_svg_emission"
    bl_label = "Import SVG File with Processing and Emission (.svg)"
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

        imported_collection.name = f"SVG_Emission_{file_name_without_ext}"
        imported_collection.processed_svg = processed_svg

        # Setup objects and materials
        for obj in imported_collection.objects:
            # Rename curve objects from "Curve" to "n"
            if obj.name.startswith("Curve"):
                obj.name = "n" + obj.name[5:]
            setup_object(obj, scale_factor=200)

        deduplicate_materials(imported_collection)




        elapsed_time_ms = (time.perf_counter() - start_time) * 1000
        self.report(
            {"INFO"},
            f" 🦢  SVG Importer (Emission): {raw_svg_file.name} rendered in {elapsed_time_ms:.2f} ms as {imported_collection.name}",
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

# File Handler for drag-and-drop support (emission SVG)
class EmissionSVG_FH_import(bpy.types.FileHandler):
    """A file handler to allow .svg files to be dragged and dropped directly into Blender with processing and emission materials."""

    bl_idname = "EmissionSVG_FH_import"
    bl_label = "File handler for SVG import with processing and emission"
    bl_import_operator = "import_scene.import_svg_emission"
    bl_file_extensions = ".svg"

    @classmethod
    def poll_drop(cls, context):
        # Allow drag–n–drop
        return context.area is not None
