import bpy

# --- Property update callback ---
def update_z_offset(self, context):
    scene = context.scene
    collection = scene.z_offset_collection
    offset = scene.z_offset_value

    if not collection:
        return

    for i, obj in enumerate(collection.objects):
        if obj.type == 'MESH' or obj.type == 'CURVE' or obj.type == 'EMPTY':
            obj.location.z = i * offset


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
        default=0.0,
        step=0.1,
        precision=3,
        update=update_z_offset
    )


def unregister():
    del bpy.types.Scene.z_offset_collection
    del bpy.types.Scene.z_offset_value
    bpy.utils.unregister_class(SCENE_PT_z_offset)

