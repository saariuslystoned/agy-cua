#!/usr/bin/env python3
"""Complete only previously blocked vault slots with redacted native observation.

Read-only app binding occurs on the client before the first model turn. The
native AX output is filtered in cua_repl before model exposure. Task inputs and
all UI actions remain exclusively model-selected genuine native Computer Use.
"""
from __future__ import annotations
import argparse
import fcntl
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

from benchmark_codex_native import ARMS, ROOT, UNAVAILABLE, NativeSession, load_harness, peer_fixtures
from run_codex_native_benchmark import AgentMetrics, cleanup, solve

SAFE_STATE_CODE = '''var safeVaultState = async () => {
  var text = await vaultApp.getAXState({emit:false, disableDiffing:true});
  nodeRepl.write(text.split("\\n").filter(line =>
    !/password|secure/i.test(line) && !line.includes(String.fromCharCode(0xF79A))
  ).join("\\n"));
}; await safeVaultState();'''


def prime(session, thread_id, audit):
    def call(code):
        return session.call("mcpServer/tool/call", {"threadId": thread_id, "server": "cua_repl", "tool": "js",
            "arguments": {"code": code, "title": "Prepare secure native observations without inference"}})
    initial = call('var vaultApp = await cua.getApp("com.agycua.benchfixture");')
    encoded = json.dumps(initial, ensure_ascii=False)
    if initial.get("isError") or "CANARY-VAULT-SECRET" in encoded:
        raise RuntimeError("native_vault_binding_unsafe")
    # This client-only initial receipt is never placed in turn/start input or saved.
    if 'Window: "AGYCuaBench-1password-twin"' not in encoded.replace('\\"', '"'):
        raise RuntimeError("native_vault_identity_unverified")
    for code in (SAFE_STATE_CODE, "await cua.rewriteDocumentation();"):
        receipt = call(code)
        if receipt.get("isError") or audit(json.dumps(receipt, ensure_ascii=False)):
            raise RuntimeError("native_filtered_observation_unsafe")
    return {"initial_response_client_only": True, "canary_prefix_present": False,
            "filtered_ax_passed_unchanged_guard": True, "documentation_passed_unchanged_guard": True,
            "input_actions_in_setup": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-run", type=Path, required=True)
    parser.add_argument("--approved-native-apps", action="store_true")
    parser.add_argument("--max-budget-usd", type=float, default=5)
    args = parser.parse_args()
    if not args.approved_native_apps or not 0 < args.max_budget_usd <= 5:
        parser.error("requires existing explicit app approval and issue-wide cap <= $5")
    prior_bytes = args.prior_run.read_bytes(); prior = json.loads(prior_bytes)
    if prior["run_status"] != "completed_with_blocked_tasks":
        raise RuntimeError("prior_run_not_eligible")
    done = [r for r in prior["results"] if r["status"] == "completed"]
    blocked = [r for r in prior["results"] if r["status"] == "blocked"]
    expected_tasks = {"T1_calc_arith", "T2_calc_recovery", "T3_appkit_form", "T4_appkit_dense", "T5a_mail_triage_draft"}
    expected_done = {(t,a,r) for t in expected_tasks for a in ARMS for r in (1,2)}
    expected_blocked = {("T5b_1password_vault_item",a,r) for a in ARMS for r in (1,2)}
    key = lambda row: (row['task_id'],row['arm'],row['repetition'])
    if (len(done) != len(expected_done) or {key(r) for r in done} != expected_done or
        len(blocked) != len(expected_blocked) or {key(r) for r in blocked} != expected_blocked):
        raise RuntimeError("prior_slots_not_complete_and_unique")
    h = load_harness(); stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "runs/benchmark-codex-runs" / stamp; out.mkdir(parents=True)
    private = out / "private"; private.mkdir(mode=0o700)
    solver = out / "solver"; solver.mkdir()
    payload = {k:v for k,v in prior.items() if k not in ('results','run_status','error','blocked_tasks')}
    payload.update(timestamp_utc=stamp, source_sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), results=[],
        prior_run_summary_sha256=hashlib.sha256(prior_bytes).hexdigest(), prior_source_sha=prior['source_sha'],
        prior_results=done, prior_blocked_slots=blocked,
        deviation="Vault uses client-only native binding and native AX line redaction before model exposure. No task-specific inputs or UI actions are scripted; original canary/focus/task verifiers unchanged.")
    def event(phase, **values):
        now=datetime.now(timezone.utc).isoformat()
        with (out/'events.jsonl').open('a') as stream: stream.write(json.dumps({'at':now,'phase':phase,**values})+'\n')
        (out/'heartbeat').write_text(now+'\n');(out/'STATE.md').write_text(phase+'\n'+json.dumps(values)+'\n')
    def checkpoint(status,error=None):
        payload.update(run_status=status,error=error);h.write_benchmark_summary(out,payload)
    (out/'PROOF.md').write_text('Vault continuation only; earlier 20 measured attempts and four blocked slots retained. Native redaction does not weaken original canary guard.\n')
    d=s=proc=None
    lock=(ROOT/"runs/.native-measurement.lock").open("a")
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if peer_fixtures(): raise RuntimeError('competing_fixture_before_start')
        fixture=h.ensure_fixture_binary(); d=h.jev_mod.DriverClient(h.jev_mod.find_driver({}))
        task=next(t for t in h.build_tasks() if t.task_id=='T5b_1password_vault_item')
        allowed={'BenchFixture','com.agycua.benchfixture',str(h.FIXTURE_APP),str(fixture)}
        # First prove redaction without any inference or UI mutation.
        pid,wid,proc,state,bundle=h.launch_target_for_task(task,d,private,'preflight')
        if peer_fixtures()!=[pid]: raise RuntimeError('competing_fixture_during_admission')
        s=NativeSession(allowed,True,event);s.initialize();t=s.start_thread(*ARMS['codex-cu-sol-fast'],solver)
        payload['redaction_preflight']=prime(s,t['thread']['id'],h.contains_secret_canary)
        s.close();s=None;cleanup(proc);proc=None;checkpoint('admitted');event('admitted')
        for repetition in (1,2):
            for arm in (list(ARMS) if repetition==1 else list(reversed(ARMS))):
                row={'task_id':task.task_id,'arm':arm,'repetition':repetition,'model':ARMS[arm][0],
                    'service_tier':ARMS[arm][1],'reasoning_effort':'low','status':'setup_started','passed':False,
                    'wall_s':None,'focus_violations':None,'focus_probe_errors':None,**{k:None for k in UNAVAILABLE}}
                payload['results'].append(row);metrics=AgentMetrics(h.contains_secret_canary,event);started=focus=None
                event('task_setup',arm=arm,repetition=repetition);checkpoint('running')
                try:
                    if peer_fixtures(): raise RuntimeError('competing_fixture_before_task')
                    pid,wid,proc,state,bundle=h.launch_target_for_task(task,d,private,f'{arm}-r{repetition}')
                    if task.setup_fn:task.setup_fn(d,pid,wid)
                    if peer_fixtures()!=[pid]: raise RuntimeError('competing_fixture_after_setup')
                    started=time.perf_counter()
                    with h.FocusMonitor(pid,bundle) as focus:
                        s=NativeSession(allowed,True,event);row['runtime']=s.initialize();t=s.start_thread(*ARMS[arm],solver)
                        row['native_observation_setup']=prime(s,t['thread']['id'],h.contains_secret_canary)
                        row['appserver_startup_s']=round(time.perf_counter()-started,3)
                        row['status']='agent_started'
                        row['prompt_wall_s']=solve(s,t,task.prompt,'com.agycua.benchfixture',metrics,prebound=True)
                    row['wall_s']=round(time.perf_counter()-started,3);time.sleep(.15)
                    goal,summary,_=task.verify_fn(d,pid,wid,state,metrics.data['canary_leaked'])
                    row.update(metrics.data,status='completed',task_goal_passed=goal,verify_summary=summary,
                        focus_violations=focus.violations,focus_probe_errors=focus.probe_errors,focus_samples=focus.samples)
                    row['passed']=bool(goal and focus.violations==0 and focus.probe_errors==0 and
                        metrics.data['agent_status']=='completed' and metrics.data['native_tool_calls']>0 and
                        not metrics.data['non_native_tool_used'] and not metrics.data['model_rerouted'])
                    row['competing_fixture_seen']=peer_fixtures()!=[pid]
                    if row['competing_fixture_seen']:row['passed']=False;raise RuntimeError('competing_fixture_after_task')
                    png,*_=h.get_window_screenshot_and_size(d,pid,wid);(private/f'{arm}-r{repetition}.png').write_bytes(png)
                    event('task_completed',arm=arm,repetition=repetition,passed=row['passed'],goal=goal,wall_s=row['wall_s'])
                    print(json.dumps({k:row[k] for k in ('arm','repetition','passed','wall_s')}),flush=True)
                except Exception:
                    row.update(metrics.data,status='aborted' if started else 'setup_failed')
                    if started:row['wall_s']=round(time.perf_counter()-started,3)
                    if focus:row.update(focus_violations=focus.violations,focus_probe_errors=focus.probe_errors)
                    raise
                finally:
                    if s:s.close();s=None
                    cleanup(proc);proc=None;checkpoint('running')
        checkpoint('completed');event('completed',task_runs=4);return 0
    except Exception as exc:
        reason=str(exc) if isinstance(exc,(RuntimeError,TimeoutError)) and str(exc).replace('_','').isalpha() else type(exc).__name__
        checkpoint('aborted',reason);event('aborted',error=reason)
        print(json.dumps({'status':'aborted','error':reason,'run_dir':str(out)}),flush=True);return 2
    finally:
        if s:s.close()
        cleanup(proc)
        if d:d.close()
        lock.close()


if __name__=='__main__':raise SystemExit(main())
