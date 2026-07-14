import bpy

from .image_import import PAINT_ORDER_Z_STEP

# --- Property update callback ---
def update_z_offset(self, context):
    scene = context.scene
    collection = scene.z_offset_collection
    offset = scene.z_offset_value

    if not collection:
        return

    eligible = [
        (index, obj)
        for index, obj in enumerate(collection.objects)
        if obj.type in {"MESH", "CURVE", "EMPTY"}
    ]
    eligible.sort(
        key=lambda item: (item[1].get("svg_paint_index", item[0]), item[0])
    )
    for paint_index, (_collection_index, obj) in enumerate(eligible):
        obj.location.z = paint_index * offset


# --- Panel ---
class SCENE_PT_z_offset(bpy.types.Panel):
    bl_label = "Z Offset"
    bl_idname = "SCENE_PT_z_offset"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "scene"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "z_offset_collection")
        layout.prop(scene, "z_offset_value")


# --- Register properties ---
def register():
    bpy.utils.register_class(SCENE_PT_z_offset)

    bpy.types.Scene.z_offset_collection = bpy.props.PointerProperty(
        name="Collection",
        type=bpy.types.Collection,
        description="Collection of objects to offset"
    )

    bpy.types.Scene.z_offset_value = bpy.props.FloatProperty(
        name="Z Offset",
        description="Offset applied per object along Z",
        default=PAINT_ORDER_Z_STEP,
        step=0.01,
        precision=6,
        update=update_z_offset
    )


def unregister():
    del bpy.types.Scene.z_offset_collection
    del bpy.types.Scene.z_offset_value
    bpy.utils.unregister_class(SCENE_PT_z_offset)
