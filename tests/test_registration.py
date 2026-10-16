"""Run with Blender's tests/run.py runner."""
import unittest
import bpy
import enhanced_svg

class FileHandlerRegistrationTests(unittest.TestCase):
    def test_file_handlers_survive_enable_disable_cycles(self):
        for _ in range(2):
            enhanced_svg.register()
            try:
                for name, operator in (
                    ('SIMPLESVG_FH_import', 'import_scene.import_simple_svg'),
                    ('EMISSIONSVG_FH_import', 'import_scene.import_svg_emission'),
                ):
                    handler = bpy.types.FileHandler.bl_rna_get_subclass_py(name)
                    self.assertIsNotNone(handler)
                    self.assertEqual(handler.bl_import_operator, operator)
                    self.assertEqual(handler.bl_file_extensions, '.svg')
            finally:
                enhanced_svg.unregister()
            self.assertIsNone(bpy.types.FileHandler.bl_rna_get_subclass_py('SIMPLESVG_FH_import'))
            self.assertIsNone(bpy.types.FileHandler.bl_rna_get_subclass_py('EMISSIONSVG_FH_import'))
