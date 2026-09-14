import Lean

/- Interactive recorder for Lean 4.19.0. Raw diagnostics are written only to
   the debugging journal. The Python driver builds model observations explicitly. -/
open Lean Meta Elab Tactic

namespace AtlasSession

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

private def field (j : Json) (key : String) : TacticM String := do
  match j.getObjValAs? String key with
  | .ok value => pure value
  | .error err => throwError "{err}"

private def emit (journal : IO.FS.Handle) (stdout : IO.FS.Handle) (event : Json) : IO Unit := do
  let line := event.compress
  journal.putStrLn line
  journal.flush
  stdout.putStrLn ("ATLAS_SESSION " ++ line)
  stdout.flush

private def exportPremises (path : String) : TacticM Nat := do
  let handle ← IO.FS.Handle.mk path .write
  let constants := (← getEnv).constants.toList.toArray.qsort fun a b => a.1.toString < b.1.toString
  let mut count := 0
  for (name, ci) in constants do
    if ci matches .thmInfo _ then
      handle.putStrLn <| (obj [("name", toJson name.toString), ("kind", toJson "theorem"),
        ("statement", toJson (← ppExpr ci.type).pretty)]).compress
      count := count + 1
  handle.flush
  return count

elab "atlas_session" : tactic => do
  let some target ← IO.getEnv "ATLAS_SESSION_TARGET" | throwError "Missing target"
  let some output ← IO.getEnv "ATLAS_SESSION_JOURNAL" | throwError "Missing journal"
  let some inputPipe ← IO.getEnv "ATLAS_SESSION_COMMANDS" | throwError "Missing command pipe"
  let some outputPipe ← IO.getEnv "ATLAS_SESSION_RESPONSES" | throwError "Missing response pipe"
  let journal ← IO.FS.Handle.mk output .write
  -- Elaboration captures standard IO. Explicit local pipes permit live interaction.
  let stdin ← IO.FS.Handle.mk inputPipe .read
  let stdout ← IO.FS.Handle.mk outputPipe .write
  if (← getEnv).contains target.toName then
    emit journal stdout <| obj [("kind", toJson "fatal"), ("error", toJson "target_already_available")]
    throwError "Target already exists in the allowed environment: {target}"
  let poolStarted ← IO.monoNanosNow
  let mut premiseCount : Option Nat := none
  if let some path ← IO.getEnv "ATLAS_SESSION_PREMISES" then
    premiseCount := some (← exportPremises path)
  let poolNs := (← IO.monoNanosNow) - poolStarted
  let initial ← snapshot
  let mut saved := #[← Tactic.saveState]
  let mut current := 0
  emit journal stdout <| obj [("kind", toJson "ready"), ("checkpoint", toJson current),
    ("target_absent", toJson true), ("snapshot", initial),
    ("premise_count", toJson premiseCount), ("premise_export_ns", toJson poolNs)]
  let mut seq := 0
  repeat
    let line ← stdin.getLine
    if line.isEmpty then
      emit journal stdout <| obj [("kind", toJson "input_closed"), ("seq", toJson seq)]
      throwError "Controller input closed with an active episode"
    let started ← IO.monoNanosNow
    let response ← try
      let .ok request := Json.parse line | throwError "Invalid command JSON"
      let op ← field request "op"
      if op == "step" then
        let action ← field request "action"
        let source := current
        let some checkpoint := saved[current]? | throwError "Missing current checkpoint"
        let before ← snapshot
        let actionStarted ← IO.monoNanosNow
        let mut error : Option String := none
        try
          let .ok stx := Parser.runParserCategory (← getEnv) `tactic action
            | throwError "Tactic parse failed: {action}"
          withoutRecover <| evalTactic stx
          pruneSolvedGoals
        catch ex =>
          error := some (← ex.toMessageData.toString)
          checkpoint.restore true
        let actionNs := (← IO.monoNanosNow) - actionStarted
        let snapStarted ← IO.monoNanosNow
        let after ← snapshot
        saved := saved.push (← Tactic.saveState)
        current := saved.size - 1
        let snapshotNs := (← IO.monoNanosNow) - snapStarted
        let accepted := error.isNone
        let expected := (request.getObjValAs? String "expected").toOption
        let acceptance := if accepted then "accepted" else "rejected"
        pure <| obj [("kind", toJson "step"), ("source_checkpoint", toJson source),
          ("checkpoint", toJson current), ("action", toJson action),
          ("tactic_acceptance", toJson acceptance), ("error", toJson error),
          ("expected_matches", toJson (expected.map (· == acceptance))),
          ("before", before), ("snapshot", after),
          ("lean_action_ns", toJson actionNs), ("checkpoint_snapshot_ns", toJson snapshotNs)]
      else if op == "restore" then
        let .ok id := request.getObjValAs? Nat "checkpoint" | throwError "Missing checkpoint"
        let some checkpoint := saved[id]? | throwError "Invalid checkpoint"
        checkpoint.restore true
        current := id
        pure <| obj [("kind", toJson "restore"), ("checkpoint", toJson current),
          ("snapshot", ← snapshot)]
      else if op == "snapshot" then
        pure <| obj [("kind", toJson "snapshot"), ("checkpoint", toJson current),
          ("snapshot", ← snapshot)]
      else if op == "stop" then
        pure <| obj [("kind", toJson "stop"), ("checkpoint", toJson current),
          ("proof_closed", toJson (← getUnsolvedGoals).isEmpty), ("snapshot", ← snapshot)]
      else throwError "Unknown operation: {op}"
    catch ex =>
      pure <| obj [("kind", toJson "protocol_error"), ("error", toJson (← ex.toMessageData.toString)),
        ("checkpoint", toJson current)]
    let response := response.setObjVal! "seq" (toJson seq)
    let response := response.setObjVal! "lean_command_ns" (toJson ((← IO.monoNanosNow) - started))
    emit journal stdout response
    seq := seq + 1
    if (response.getObjValAs? String "kind").toOption == some "stop" then
      if (← getUnsolvedGoals).isEmpty then return
      throwError "Session stopped with open goals; no proof was admitted"

end AtlasSession
