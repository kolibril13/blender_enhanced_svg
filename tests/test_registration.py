"""Run with Blender's tests/run.py runner."""
import unittest
import bpy
import enhanced_svg

HANDLERS = (
    ('SIMPLESVG_FH_import', 'import_scene.import_simple_svg'),
    ('SVG_FH_import', 'import_scene.import_svg'),
    ('EMISSIONSVG_FH_import', 'import_scene.import_svg_emission'),
)

class FileHandlerRegistrationTests(unittest.TestCase):
    def test_file_handlers_survive_enable_disable_cycles(self):
        for _ in range(2):
            enhanced_svg.register()
            try:
                for name, operator in HANDLERS:
                    handler = bpy.types.FileHandler.bl_rna_get_subclass_py(name)
                    self.assertIsNotNone(handler)
                    self.assertEqual(handler.bl_import_operator, operator)
                    self.assertEqual(handler.bl_file_extensions, '.svg')
            finally:
                enhanced_svg.unregister()
            for name, _operator in HANDLERS:
                self.assertIsNone(bpy.types.FileHandler.bl_rna_get_subclass_py(name))

    def test_import_operators_are_registered(self):
        enhanced_svg.register()
        try:
            for _name, operator in HANDLERS:
                module, _, function = operator.partition('.')
                self.assertTrue(
                    hasattr(getattr(bpy.ops, module), function),
                    f'{operator} is not registered',
                )
        finally:
            enhanced_svg.unregister()

    def test_import_menu_entries_are_removed_on_unregister(self):
        before = list(bpy.types.TOPBAR_MT_file_import._dyn_ui_initialize())
        enhanced_svg.register()
        try:
            during = list(bpy.types.TOPBAR_MT_file_import._dyn_ui_initialize())
            self.assertEqual(len(during), len(before) + 1)
        finally:
            enhanced_svg.unregister()
        after = list(bpy.types.TOPBAR_MT_file_import._dyn_ui_initialize())
        self.assertEqual(after, before)
