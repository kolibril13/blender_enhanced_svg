import bpy
from mathutils import Matrix


def latest_collection_set_position(position=(0, 0, 0)):
    collection = bpy.context.collection.children[-1]
    for obj in collection.objects:
        obj.data.transform(Matrix.Scale(100, 4))
        obj.location = position
