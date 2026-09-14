import Lean

/- Replay instrumentation for the pinned Lean 4.19.0 pilot.
   All actions execute in Lean. Failed actions restore the full tactic state.
   Durable replay uses the source prefix and the ordered, branching action plan.
   Process-local metavariable names are diagnostics, never checkpoint handles. -/
open Lean Meta Elab Tactic

namespace AtlasReplay

private def obj := Json.mkObj

private def snapshot : TacticM Json := do
  let goals ← getUnsolvedGoals
  let mut views := #[]
  for g in goals do
    let view ← g.withContext do
      let mut locals := #[]
      for d in (← getLCtx) do
        locals := locals.push <| obj [
          ("name", toJson d.userName.toString),
          ("local_id", toJson d.fvarId.name.toString),
          ("type", toJson (← ppExpr (← instantiateMVars d.type)).pretty),
          ("type_repr", toJson (reprStr d.type)),
          ("value_repr", toJson (d.value?.map reprStr)),
          ("binder_info", toJson (reprStr d.binderInfo)),
          ("implementation_detail", toJson d.isImplementationDetail)]
      let type ← g.getType
      return obj [
        ("process_goal_id", toJson g.name.toString),
        ("display", toJson (← ppGoal g).pretty),
        ("target", toJson (← ppExpr (← instantiateMVars type)).pretty),
        ("target_repr", toJson (reprStr type)),
        ("local_context", Json.arr locals)]
    views := views.push view
  let mctx ← getMCtx
  let declarations := mctx.decls.toArray.map fun (id, d) => obj [
    ("id", toJson id.name.toString), ("type_repr", toJson (reprStr d.type)),
    ("depth", toJson d.depth), ("index", toJson d.index),
    ("assignment_repr", toJson ((mctx.eAssignment.find? id).map reprStr)),
    ("delayed_assignment", toJson ((mctx.dAssignment.find? id).map fun a =>
      obj [("pending_id", toJson a.mvarIdPending.name.toString),
           ("fvars_repr", toJson (a.fvars.map reprStr))]))]
  return obj [("goals", Json.arr views),
    ("metavariables", obj [("depth", toJson mctx.depth),
      ("counter", toJson mctx.mvarCounter), ("declarations", Json.arr declarations),
      ("universe_assignments", toJson (mctx.lAssignment.toArray.map fun (id,l) =>
        obj [("id", toJson id.name.toString), ("value_repr", toJson (reprStr l))]))]),
    ("checkpoint_encoding", toJson "source-prefix-and-action-history; repr fields are diagnostics")]

private def getString (j : Json) (key : String) : TacticM String := do
  match j.getObjValAs? String key with
  | .ok s => pure s
  | .error e => throwError "{e}"

elab "atlas_pilot" : tactic => do
  let some path ← IO.getEnv "ATLAS_REPLAY_PLAN" | throwError "ATLAS_REPLAY_PLAN is required"
  let some output ← IO.getEnv "ATLAS_REPLAY_OUTPUT" | throwError "ATLAS_REPLAY_OUTPUT is required"
  let .ok plan := Json.parse (← IO.FS.readFile path) | throwError "Invalid replay plan JSON"
  let .ok steps := plan.getObjValAs? (Array Json) "actions" | throwError "Missing actions"
  -- This is a hard guard against loading the held-out answer through imports.
  if (← getEnv).contains `Nat.Prime.dvd_mul then
    throwError "Target already exists in the allowed environment"
  let initial ← snapshot
  let mut saved := #[← Tactic.saveState]
  let mut events := #[]
  let mut current := 0
  for i in [:steps.size] do
    let step := steps[i]!
    let .ok source := step.getObjValAs? Nat "from" | throwError "Missing source state"
    let some checkpoint := saved[source]? | throwError "Forward or invalid state reference"
    let action ← getString step "action"
    let expected ← getString step "expect"
    let started ← IO.monoMsNow
    checkpoint.restore true
    let before ← snapshot
    let mut error : Option String := none
    try
      let .ok stx := Parser.runParserCategory (← getEnv) `tactic action
        | throwError "Tactic parse failed: {action}"
      withoutRecover <| evalTactic stx
      pruneSolvedGoals
    catch ex =>
      error := some (← ex.toMessageData.toString)
      checkpoint.restore true
    let elapsed := (← IO.monoMsNow) - started
    let after ← snapshot
    let status := if error.isSome then "error" else "success"
    if expected != status then
      throwError "Action {i}: expected {expected}, got {status}: {error}"
    events := events.push <| obj [
      ("index", toJson i), ("source_state", toJson source),
      ("restored_from_other_branch", toJson (source != current)),
      ("action", toJson action), ("outcome", toJson status),
      ("error", toJson error), ("before", before), ("after", after),
      ("elapsed_ms", toJson elapsed)]
    saved := saved.push (← Tactic.saveState)
    current := i + 1
  unless (← getUnsolvedGoals).isEmpty do throwError "Pilot ended with unsolved goals"
  IO.FS.writeFile output <| (obj [("initial", initial), ("events", Json.arr events),
    ("target_absent_at_start", toJson true)]).compress ++ "\n"

elab "atlas_available" : command => Command.liftTermElabM do
  let some output ← IO.getEnv "ATLAS_PREMISE_OUTPUT" | throwError "ATLAS_PREMISE_OUTPUT is required"
  if (← getEnv).contains `Nat.Prime.dvd_mul then throwError "Target already imported"
  let handle ← IO.FS.Handle.mk output .write
  let constants := (← getEnv).constants.toList.toArray.qsort fun a b => a.1.toString < b.1.toString
  for (n, ci) in constants do
    if ci matches .thmInfo _ then
      handle.putStrLn <| (obj [("name", toJson n.toString), ("kind", toJson "theorem"),
        ("statement", toJson (← ppExpr ci.type).pretty)]).compress

end AtlasReplay
