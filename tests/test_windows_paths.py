"""A short Windows pathname remains a valid project input, not an escape."""
from pathlib import Path
import ctypes,os,sys,tempfile,unittest
RELEASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(RELEASE/'research-evidence-workflow/scripts/tools'))
import core,research_flow as flow

@unittest.skipUnless(os.name=='nt','Windows 8.3 path aliases')
class WindowsPathAlias(unittest.TestCase):
    def test_short_alias_project_can_register_pack_and_restore(self):
        with tempfile.TemporaryDirectory(prefix='Public long alias control ') as tmp:
            root=Path(tmp).resolve();buffer=ctypes.create_unicode_buffer(32768)
            function=ctypes.windll.kernel32.GetShortPathNameW
            function.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint32];function.restype=ctypes.c_uint32
            size=function(str(root),buffer,len(buffer))
            if not size or size>=len(buffer):self.skipTest('8.3 alias unavailable on this volume')
            alias=Path(buffer.value)
            if str(alias).casefold()==str(root).casefold():self.skipTest('Volume returned no distinct alias')
            project=alias/'project';flow.initialize(project,'Synthetic short-path acceptance','short-path')
            source=project/'scope.md';source.write_text('Synthetic bounded scope; no research measurements.',encoding='utf-8')
            state=project/'research_state.json';flow.register_artifact(state,source,'feasibility','F','Synthetic scope')
            flow.set_stage(state,'scope','complete','Synthetic scope record only',['F'])
            self.assertTrue(flow.check_state(state)['passed'])
            package=alias/'handoff.zip';flow.pack(state,package,True)
            dest=alias/'received';flow.restore(package,dest)
            self.assertTrue(flow.check_state(dest/'project/research_state.json')['passed'])
            self.assertEqual((dest/'project/scope.md').read_bytes(),source.read_bytes())
            with self.assertRaises(ValueError):core.inside(project,'../outside.md')

if __name__=='__main__':unittest.main(verbosity=2)
