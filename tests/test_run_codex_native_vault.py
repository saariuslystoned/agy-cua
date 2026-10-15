import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
try:
    spec=importlib.util.spec_from_file_location('run_codex_native_vault',SCRIPTS/'run_codex_native_vault.py')
    vault=importlib.util.module_from_spec(spec);spec.loader.exec_module(vault)
finally:sys.path.remove(str(SCRIPTS))


def audit(text):
    return 'CANARY-VAULT-SECRET' in text or chr(0xF79A) in text


def receipt(text):return {'content':[{'type':'text','text':text}]}


class VaultTests(unittest.TestCase):
    def test_production_helper_preserves_public_state_and_hides_secure_lines(self):
        state='1: AXTextField Title value=public\n2: AXSecureTextField Password value=masked\n3: AXTextField value='+chr(0xF79A)+'\n4: AXButton Save'
        js='var nodeRepl={write: s=>process.stdout.write(s)}; var vaultApp={getAXState:async opts=>{if(opts.emit!==false||opts.disableDiffing!==true)throw Error("unsafe options");return '+json.dumps(state)+';}};\n'+vault.SAFE_STATE_CODE
        result=subprocess.run(['node','--input-type=module','-e',js],capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout,'1: AXTextField Title value=public\n4: AXButton Save')
        self.assertFalse(audit(result.stdout))

    def test_production_helper_propagates_native_observation_errors(self):
        js='var nodeRepl={write:()=>{throw Error("must not emit")}}; var vaultApp={getAXState:async()=>{throw Error("native observation unavailable")}};\n'+vault.SAFE_STATE_CODE
        result=subprocess.run(['node','--input-type=module','-e',js],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout,'')

    def test_client_setup_uses_only_native_reads_and_accepts_redacted_output(self):
        session=Mock(); session.call.side_effect=[receipt('Window: "AGYCuaBench-1password-twin" '+chr(0xF79A)),receipt('1: AXTextField Title'),receipt('Native API documentation')]
        result=vault.prime(session,'fresh-thread',audit)
        self.assertTrue(result['filtered_ax_passed_unchanged_guard'])
        self.assertEqual(result['input_actions_in_setup'],0)
        self.assertEqual([c.args[0] for c in session.call.call_args_list],['mcpServer/tool/call']*3)
        self.assertTrue(all(c.args[1]['server']=='cua_repl' for c in session.call.call_args_list))

    def test_binding_error_wrong_window_and_canary_fail_before_model_turn(self):
        for value in ({'isError':True},receipt('wrong window'),receipt('Window: "AGYCuaBench-1password-twin" CANARY-VAULT-SECRET-SYNTHETIC')):
            with self.subTest(value_type=list(value)):
                session=Mock();session.call.return_value=value
                with self.assertRaises(RuntimeError):vault.prime(session,'fresh',audit)
                self.assertEqual(session.call.call_count,1)

    def test_filtered_marker_or_tool_error_is_never_admitted(self):
        for bad in (receipt(chr(0xF79A)),{'isError':True}):
            session=Mock();session.call.side_effect=[receipt('Window: "AGYCuaBench-1password-twin"'),bad]
            with self.assertRaisesRegex(RuntimeError,'native_filtered_observation_unsafe'):vault.prime(session,'fresh',audit)


if __name__=='__main__':unittest.main()
