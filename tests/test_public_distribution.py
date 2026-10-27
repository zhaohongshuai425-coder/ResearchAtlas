"""Public distributions and copied handoff code must retain their notices."""
from pathlib import Path
import hashlib,importlib.util,json,re,sys,tempfile,unittest
from unittest.mock import patch
RELEASE=Path(__file__).resolve().parents[1];SKILL=RELEASE/'research-evidence-workflow';TOOLS=SKILL/'scripts/tools'
sys.path.insert(0,str(TOOLS))
import research_flow as flow
from release_fixtures import completed_release
spec=importlib.util.spec_from_file_location('public_distribution_installer',RELEASE/'install.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class PublicDistribution(unittest.TestCase):
    def test_generated_harness_links_resolve_and_receipt_binds_new_bytes(self):
        spec=importlib.util.spec_from_file_location('public_link_builder',RELEASE/'build_release.py')
        builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
        with tempfile.TemporaryDirectory(prefix='Public links ') as tmp,patch.object(builder,'SOURCE',RELEASE):
            output=Path(tmp)/'release';builder.build(output,test=False)
            text=(output/'Harness_执行入口.md').read_text(encoding='utf-8')
            local=[]
            for target in re.findall(r'\]\(([^\n)]*)\)',text):
                target=target.strip('<>')
                if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:',target) or target.startswith('#'):continue
                local.append(target);self.assertTrue((output/target).is_file(),target)
            self.assertGreaterEqual(len(local),4)
            receipt=json.loads((output/'Harness_执行入口.md.receipt.json').read_text(encoding='utf-8'))
            self.assertEqual(receipt['output_sha256'],hashlib.sha256((output/'Harness_执行入口.md').read_bytes()).hexdigest())
            self.assertEqual(receipt['output_characters'],len(text))
            manifest=json.loads((output/'manifest.json').read_text(encoding='utf-8'))
            for path in manifest['files']:
                data=(output/path).read_bytes()
                self.assertNotIn(b'\r\n',data,path)
                self.assertEqual(hashlib.sha256(data.replace(b'\r\n',b'\n')).hexdigest(),manifest['files'][path],path)
    def test_installed_skill_retains_licenses_without_private_template(self):
        with tempfile.TemporaryDirectory(prefix='Public install ') as tmp,completed_release(RELEASE) as accepted,patch.object(installer,'ROOT',accepted):
            result=installer.install(tmp);root=Path(result['installed'])
            self.assertEqual((root/'LICENSE').read_bytes(),(RELEASE/'LICENSE').read_bytes())
            self.assertEqual((root/'scripts/tools/LICENSE').read_bytes(),(RELEASE/'LICENSE').read_bytes())
            notice=(root/'scripts/tools/THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
            self.assertIn('Copyright (c) 2025 K-Dense Inc.',notice)
            self.assertEqual(list(root.rglob('*.docx')),[])
            self.assertTrue((root/'scripts/tools/references/学位论文默认格式_沈阳农业大学本地版.md').is_file())
    def test_project_handoff_carries_code_notices_and_text_preset(self):
        with tempfile.TemporaryDirectory(prefix='Public handoff ') as tmp:
            root=Path(tmp);flow.initialize(root/'project','Synthetic public handoff','public-notices')
            package=root/'handoff.zip';flow.pack(root/'project/research_state.json',package,True)
            dest=root/'received';flow.restore(package,dest)
            self.assertEqual((dest/'tools/LICENSE').read_bytes(),(RELEASE/'LICENSE').read_bytes())
            self.assertEqual((dest/'tools/THIRD_PARTY_NOTICES.md').read_bytes(),(TOOLS/'THIRD_PARTY_NOTICES.md').read_bytes())
            self.assertTrue((dest/'tools/references/学位论文默认格式_沈阳农业大学本地版.md').is_file())
            self.assertEqual(list(dest.rglob('*.docx')),[])
            self.assertTrue(flow.verify_package(package)['passed'])

if __name__=='__main__':unittest.main(verbosity=2)
