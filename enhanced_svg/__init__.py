import bpy


from .imports import (
    ImportSVGOperator,
    SVG_FH_import,
    ImportSimpleSVGOperator,
    SimpleSVG_FH_import,
    ImportSVGEmissionOperator,
    EmissionSVG_FH_import,
)
from . import z_offset


def register():
    # Register Blender classes
    bpy.utils.register_class(ImportSimpleSVGOperator)
    bpy.utils.register_class(SimpleSVG_FH_import)
    bpy.utils.register_class(ImportSVGOperator)
    bpy.utils.register_class(SVG_FH_import)
    bpy.utils.register_class(ImportSVGEmissionOperator)
    bpy.utils.register_class(EmissionSVG_FH_import)
    # Register Z offset panel and properties
    z_offset.register()


def unregister():
    # Unregister Z offset panel and properties
    z_offset.unregister()
    # Unregister Blender classes
    bpy.utils.unregister_class(EmissionSVG_FH_import)
    bpy.utils.unregister_class(ImportSVGEmissionOperator)
    bpy.utils.unregister_class(SVG_FH_import)
    bpy.utils.unregister_class(ImportSVGOperator)
    bpy.utils.unregister_class(SimpleSVG_FH_import)
    bpy.utils.unregister_class(ImportSimpleSVGOperator)


if __name__ == "__main__":
    register()
