import bpy
from bpy_extras.io_utils import ImportHelper
from bpy.props import BoolProperty, StringProperty
from pathlib import Path
import importlib
import os
import tempfile
from mathutils import Matrix

import time

from .svg_preprocessing import preprocess_svg
from .image_import import (
    create_image_planes,
    finalize_paint_order,
    prepare_svg_images,
)


def _select_import_collection(collections, source_name):
    """Return Blender's uniquely named collection for one temporary SVG."""
    return next(
        (collection for collection in collections if collection.name == source_name),
        None,
    )


def _import_curve_svg(context, svg_content, import_state):
    """Import SVG text through Blender using a unique, always-cleaned temp file."""
    temporary = tempfile.NamedTemporaryFile(
        mode="w", suffix=".svg", encoding="utf-8", delete=False
    )
    try:
        temporary.write(svg_content)
        temporary.close()
        collections_before = set(context.scene.collection.children)
        material_hook = None
        try:
            svg_import_module = importlib.import_module("io_curve_svg.import_svg")
            original_get_material = svg_import_module.SVGGetMaterial

            def tracked_get_material(color, import_context):
                material = original_get_material(color, import_context)
                if (
                    material is not None
                    and material not in import_state["materials"]
                ):
                    material["enhanced_svg_blender_material"] = True
                return material

            svg_import_module.SVGGetMaterial = tracked_get_material
            material_hook = (
                svg_import_module,
                original_get_material,
                tracked_get_material,
            )
        except (AttributeError, ImportError):
            # The collection/object ownership path still works if Blender
            # reorganizes its bundled SVG module; only an orphan created before
            # a parser failure would then be unavailable to tag.
            material_hook = None
        try:
            bpy.ops.import_curve.svg(filepath=temporary.name)
        finally:
            # Blender names its collection after the source filename.  Record
            # only that uniquely named collection, not every data-block that a
            # handler or another add-on may have created during the operator.
            source_name = Path(temporary.name).name
            new_collections = [
                collection
                for collection in context.scene.collection.children
                if collection not in collections_before
            ]
            imported_collection = _select_import_collection(
                new_collections, source_name
            )
            if imported_collection is not None:
                import_state["owned_collections"].add(imported_collection)
            if material_hook is not None:
                module, original, tracked = material_hook
                if module.SVGGetMaterial is tracked:
                    module.SVGGetMaterial = original
    finally:
        temporary.close()
        try:
            os.unlink(temporary.name)
        except OSError:
            pass

    new_collections = list(import_state["owned_collections"])
    if not new_collections:
        raise RuntimeError("Failed to import SVG file")
    return new_collections[0]


def _prepare_processed_import(
    context, raw_svg_file, allow_external_images, import_state
):
    raw_svg_content = raw_svg_file.read_text(encoding="utf-8")
    processed_svg = preprocess_svg(raw_svg_content)
    images, warnings, marked_svg, marker_ids = prepare_svg_images(
        processed_svg,
        svg_dir=raw_svg_file.parent,
        scene_scale_length=context.scene.unit_settings.scale_length,
        allow_external_outside_svg=allow_external_images,
    )
    imported_collection = _import_curve_svg(context, marked_svg, import_state)
    return (
        processed_svg,
        imported_collection,
        list(imported_collection.objects),
        images,
        warnings,
        marker_ids,
    )


def _snapshot_import_state():
    """Capture data-blocks that a processed import may create."""
    return {
        "collections": set(bpy.data.collections),
        "objects": set(bpy.data.objects),
        "curves": set(bpy.data.curves),
        "meshes": set(bpy.data.meshes),
        "materials": set(bpy.data.materials),
        "images": set(bpy.data.images),
        "owned_collections": set(),
    }


def _rollback_import_state(before):
    """Remove only data-blocks owned by a failed processed import."""
    owned_collections = set()

    def collect_collection(collection):
        if collection in owned_collections:
            return
        owned_collections.add(collection)
        for child in collection.children:
            # A handler may link an existing user collection below the
            # collection created by Blender's SVG importer.  Following that
            # link during rollback must not turn the pre-existing collection
            # (or any of its contents) into import-owned data.
            if child not in before["collections"]:
                collect_collection(child)

    for collection in before["owned_collections"]:
        if collection.name in bpy.data.collections:
            collect_collection(collection)

    new_objects = set(bpy.data.objects) - before["objects"]
    owned_objects = {
        obj
        for collection in owned_collections
        for obj in collection.objects
        if obj in new_objects
    }
    owned_objects.update(
        obj
        for obj in new_objects
        if obj.get("enhanced_svg_image_object")
    )

    owned_meshes = set()
    owned_curves = set()
    owned_materials = set()
    owned_images = set()
    for obj in owned_objects:
        data = obj.data
        if isinstance(data, bpy.types.Mesh):
            owned_meshes.add(data)
        elif isinstance(data, bpy.types.Curve):
            owned_curves.add(data)
        if data is not None and hasattr(data, "materials"):
            owned_materials.update(
                material for material in data.materials if material is not None
            )

    for material in owned_materials:
        if not material.use_nodes or material.node_tree is None:
            continue
        for node in material.node_tree.nodes:
            image = getattr(node, "image", None)
            if image is not None:
                owned_images.add(image)

    for obj in owned_objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in tuple(owned_collections):
        if collection in bpy.data.collections.values():
            bpy.data.collections.remove(collection)

    # Also catch importer-owned data created immediately before a later API
    # call failed, when it may not yet be reachable from a linked object.
    owned_meshes.update(
        mesh
        for mesh in set(bpy.data.meshes) - before["meshes"]
        if mesh.get("enhanced_svg_image_mesh")
    )
    owned_materials.update(
        material
        for material in set(bpy.data.materials) - before["materials"]
        if material.get("enhanced_svg_image_material")
        or material.get("enhanced_svg_curve_material")
        or material.get("enhanced_svg_blender_material")
    )
    owned_images.update(
        image
        for image in set(bpy.data.images) - before["images"]
        if image.get("enhanced_svg_source_hash")
    )

    for mesh in owned_meshes:
        if mesh not in before["meshes"] and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for curve in owned_curves:
        if curve not in before["curves"] and curve.users == 0:
            bpy.data.curves.remove(curve)
    for material in owned_materials:
        if material not in before["materials"] and material.users == 0:
            bpy.data.materials.remove(material)
    for image in owned_images:
        if image not in before["images"] and image.users == 0:
            bpy.data.images.remove(image)


def _remove_unused_import_materials(import_state):
    """Remove tagged materials made obsolete by successful marker deletion."""
    for material in tuple(set(bpy.data.materials) - import_state["materials"]):
        if material.users == 0 and (
            material.get("enhanced_svg_blender_material")
            or material.get("enhanced_svg_curve_material")
        ):
            bpy.data.materials.remove(material)


def _execute_processed_import(operator, context, use_emission):
    """Run a processed import transaction and roll it back on any failure."""
    raw_svg_file = Path(operator.filepath)
    file_name_without_ext = raw_svg_file.stem
    start_time = time.perf_counter()
    before = _snapshot_import_state()

    try:
        (
            processed_svg,
            imported_collection,
            source_objects,
            images,
            image_warnings,
            marker_ids,
        ) = _prepare_processed_import(
            context,
            raw_svg_file,
            operator.allow_external_images,
            before,
        )

        mode_name = "Emission" if use_emission else "Processed"
        imported_collection.name = f"SVG_{mode_name}_{file_name_without_ext}"
        imported_collection["processed_svg"] = processed_svg

        if use_emission:
            for obj in imported_collection.objects:
                if obj.name.startswith("Curve"):
                    obj.name = "n" + obj.name[5:]
                setup_object(obj, scale_factor=1)
            deduplicate_materials(imported_collection)

        image_objects = create_image_planes(
            images,
            imported_collection,
            use_emission=use_emission,
            warnings=image_warnings,
        )
        if marker_ids:
            ordered = finalize_paint_order(
                imported_collection,
                source_objects,
                images,
                marker_ids,
                image_warnings,
            )
            image_objects = [
                obj for obj in ordered if obj.get("svg_marker_id") is not None
            ]
        _remove_unused_import_materials(before)

        for warning in image_warnings:
            operator.report({"WARNING"}, warning)

        elapsed_time_ms = (time.perf_counter() - start_time) * 1000
        operator.report(
            {"INFO"},
            f" 🦢  SVG Importer ({mode_name}): {raw_svg_file.name} "
            f"rendered in {elapsed_time_ms:.2f} ms as "
            f"{imported_collection.name} ({len(image_objects)} images)",
        )
        return {"FINISHED"}
    except Exception:
        _rollback_import_state(before)
        raise


def deduplicate_materials(collection: bpy.types.Collection) -> None:
    """
    Deduplicate materials in a collection by reusing identical materials and giving them descriptive names.

    Args:
        collection: The collection containing objects whose materials need deduplication
    """

    materials_dict = {}
    replaced_materials = set()

    for obj in collection.objects:
        if not obj.data.materials:
            continue

        current_mat = obj.data.materials[0]
        if current_mat is None:
            continue

        mat_key = tuple(current_mat.diffuse_color)

        if mat_key not in materials_dict:
            rgb = current_mat.diffuse_color[:3]
            hex_color = "".join(f"{int(c*255):02x}" for c in rgb)
            mat_name = f"Mat{len(materials_dict)}_#{hex_color}"
            materials_dict[mat_key] = create_material(
                current_mat.diffuse_color, mat_name
            )

        new_mat = materials_dict[mat_key]
        if current_mat != new_mat:
            replaced_materials.add(current_mat)
            obj.data.materials.clear()
            obj.data.materials.append(new_mat)

    # Remove only the materials this import replaced; a global orphans_purge
    # would also delete unrelated unused data-blocks from the user's file.
    for mat in replaced_materials:
        if mat.users == 0:
            bpy.data.materials.remove(mat)


# Core object and material setup functions
def setup_object(obj: bpy.types.Object, scale_factor: float = 1) -> None:
    """Setup individual object properties."""
    obj.data.transform(Matrix.Scale(scale_factor, 4))
    obj["opacity"] = 1.0
    obj.id_properties_ui("opacity").update(min=0.0, max=1.0, step=0.1)


def create_material(color, name: str = "") -> bpy.types.Material:
    """Create a new material with nodes setup for opacity."""
    mat = bpy.data.materials.new(name=name)
    mat["enhanced_svg_curve_material"] = True
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
    allow_external_images: BoolProperty(
        name="Allow Images Outside SVG Folder",
        description=(
            "Allow absolute paths and parent-directory image references; "
            "leave disabled for untrusted SVG files"
        ),
        default=False,
    )

    def execute(self, context):
        # Verify that the selected file is a .svg file.
        if not self.filepath.lower().endswith(".svg"):
            self.report({"WARNING"}, "Selected file is not an SVG file")
            return {"CANCELLED"}

        return _execute_processed_import(self, context, use_emission=False)

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
    allow_external_images: BoolProperty(
        name="Allow Images Outside SVG Folder",
        description=(
            "Allow absolute paths and parent-directory image references; "
            "leave disabled for untrusted SVG files"
        ),
        default=False,
    )

    def execute(self, context):
        # Verify that the selected file is a .svg file.
        if not self.filepath.lower().endswith(".svg"):
            self.report({"WARNING"}, "Selected file is not an SVG file")
            return {"CANCELLED"}

        return _execute_processed_import(self, context, use_emission=True)

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

        # Snapshot existing collections so the new one can be found by diffing;
        # looking it up by name breaks when a collection with the same name
        # already exists and Blender appends a .001 suffix.
        collections_before = set(context.scene.collection.children)

        # Import SVG directly without preprocessing
        bpy.ops.import_curve.svg(filepath=str(svg_file))

        new_collections = [
            coll
            for coll in context.scene.collection.children
            if coll not in collections_before
        ]
        if not new_collections:
            raise RuntimeError("Failed to import SVG file")
        imported_collection = new_collections[0]

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
