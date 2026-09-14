import Mathlib.Data.Nat.Prime.Infinite
import Mathlib.Data.Nat.Prime.Basic
import Mathlib.Data.Nat.Prime.Pow
import Mathlib.Data.Nat.GCD.Basic
import Mathlib.Data.Nat.ModEq
import Mathlib.Data.Set.Basic
import Mathlib.Algebra.Group.Hom.Defs
import Mathlib.Algebra.Ring.Defs
import Mathlib.LinearAlgebra.FiniteDimensional.Lemmas
import Mathlib.Analysis.Calculus.Deriv.MeanValue
import Mathlib.Analysis.SpecialFunctions.ExpDeriv
import Mathlib.Topology.Order.IntermediateValue
import Mathlib.Topology.Compactness.Compact
import Mathlib.Topology.Connected.Basic
import Mathlib.Probability.ProbabilityMassFunction.Basic
import Mathlib.Data.Finset.Powerset
import Mathlib.Data.Finset.Prod
import Mathlib.Data.Finset.Card
import Lean.Replay
import Lean.Util.CollectAxioms
import Lean.DeclarationRange

/-!
An elaborated-declaration exporter. The selected closure is replayed into an
empty, trust-level-zero Lean environment before any success report is written.
Readable renderings are accompanied by a shared DAG of kernel expressions.
Expression metadata (which is irrelevant to kernel checking) is omitted.
-/

open Lean Elab Command

set_option maxRecDepth 100000
set_option maxHeartbeats 0

/-
Adapted from Lean/Replay.lean, copyright (c) 2023 Kim Morrison.
Released under Apache 2.0. Uses Kernel.Environment directly to avoid
elaborator private-name normalization across imported modules.
Every addition goes through the unmodified kernel addDeclCore checker.
-/
namespace AtlasKernelReplay

namespace Replay

structure Context where
  newConstants : Std.HashMap Name ConstantInfo

structure State where
  env : Kernel.Environment
  remaining : NameSet := {}
  pending : NameSet := {}
  postponedConstructors : NameSet := {}
  postponedRecursors : NameSet := {}

abbrev M := ReaderT Context <| StateRefT State IO

/-- Check if a `Name` still needs processing. If so, move it from `remaining` to `pending`. -/
def isTodo (name : Name) : M Bool := do
  let r := (← get).remaining
  if r.contains name then
    modify fun s => { s with remaining := s.remaining.erase name, pending := s.pending.insert name }
    return true
  else
    return false

/-- Use the current `Environment` to throw a `Kernel.Exception`. -/
def throwKernelException (ex : Kernel.Exception) : M Unit := do
  throw <| .userError <| (← ex.toMessageData {} |>.toString)

/-- Add a declaration, possibly throwing a `Kernel.Exception`. -/
def addDecl (d : Declaration) : M Unit := do
  match (← get).env.addDeclCore 0 d (cancelTk? := none) with
  | .ok env => modify fun s => { s with env := env }
  | .error ex => throwKernelException ex

mutual
/--
Check if a `Name` still needs to be processed (i.e. is in `remaining`).

If so, recursively replay any constants it refers to,
to ensure we add declarations in the right order.

The construct the `Declaration` from its stored `ConstantInfo`,
and add it to the environment.
-/
partial def replayConstant (name : Name) : M Unit := do
  if ← isTodo name then
    let some ci := (← read).newConstants[name]? | unreachable!
    replayConstants ci.getUsedConstantsAsSet
    -- Check that this name is still pending: a mutual block may have taken care of it.
    if (← get).pending.contains name then
      match ci with
      | .defnInfo   info =>
        addDecl (Declaration.defnDecl   info)
      | .thmInfo    info =>
        addDecl (Declaration.thmDecl    info)
      | .axiomInfo  info =>
        addDecl (Declaration.axiomDecl  info)
      | .opaqueInfo info =>
        addDecl (Declaration.opaqueDecl info)
      | .inductInfo info =>
        let lparams := info.levelParams
        let nparams := info.numParams
        let all ← info.all.mapM fun n => do pure <| ((← read).newConstants[n]!)
        for o in all do
          modify fun s =>
            { s with remaining := s.remaining.erase o.name, pending := s.pending.erase o.name }
        let ctorInfo ← all.mapM fun ci => do
          pure (ci, ← ci.inductiveVal!.ctors.mapM fun n => do
            pure ((← read).newConstants[n]!))
        -- Make sure we are really finished with the constructors.
        for (_, ctors) in ctorInfo do
          for ctor in ctors do
            replayConstants ctor.getUsedConstantsAsSet
        let types : List InductiveType := ctorInfo.map fun ⟨ci, ctors⟩ =>
          { name := ci.name
            type := ci.type
            ctors := ctors.map fun ci => { name := ci.name, type := ci.type } }
        addDecl (Declaration.inductDecl lparams nparams types false)
      -- We postpone checking constructors,
      -- and at the end make sure they are identical
      -- to the constructors generated when we replay the inductives.
      | .ctorInfo info =>
        modify fun s => { s with postponedConstructors := s.postponedConstructors.insert info.name }
      -- Similarly we postpone checking recursors.
      | .recInfo info =>
        modify fun s => { s with postponedRecursors := s.postponedRecursors.insert info.name }
      | .quotInfo _ =>
        addDecl (Declaration.quotDecl)
      modify fun s => { s with pending := s.pending.erase name }

/-- Replay a set of constants one at a time. -/
partial def replayConstants (names : NameSet) : M Unit := do
  for n in names do replayConstant n

end

/--
Check that all postponed constructors are identical to those generated
when we replayed the inductives.
-/
def checkPostponedConstructors : M Unit := do
  for ctor in (← get).postponedConstructors do
    match (← get).env.find? ctor, (← read).newConstants[ctor]? with
    | some (.ctorInfo info), some (.ctorInfo info') =>
      if ! (info == info') then throw <| IO.userError s!"Invalid constructor {ctor}"
    | _, _ => throw <| IO.userError s!"No such constructor {ctor}"

/--
Check that all postponed recursors are identical to those generated
when we replayed the inductives.
-/
def checkPostponedRecursors : M Unit := do
  for ctor in (← get).postponedRecursors do
    match (← get).env.find? ctor, (← read).newConstants[ctor]? with
    | some (.recInfo info), some (.recInfo info') =>
      if ! (info == info') then throw <| IO.userError s!"Invalid recursor {ctor}"
    | _, _ => throw <| IO.userError s!"No such recursor {ctor}"

end Replay

open Replay

/--
"Replay" some constants into an `Environment`, sending them to the kernel for checking.

Throws a `IO.userError` if the kernel rejects a constant,
or if there are malformed recursors or constructors for inductive types.
-/
def replay (newConstants : Std.HashMap Name ConstantInfo) (env : Kernel.Environment) : IO Kernel.Environment := do
  let mut remaining : NameSet := ∅
  for (n, ci) in newConstants.toList do
    -- We skip unsafe constants, and also partial constants.
    -- Later we may want to handle partial constants.
    if !ci.isUnsafe && !ci.isPartial then
      remaining := remaining.insert n
  let (_, s) ← StateRefT'.run (s := { env, remaining }) do
    ReaderT.run (r := { newConstants }) do
      for n in remaining do
        replayConstant n
      checkPostponedConstructors
      checkPostponedRecursors
  return s.env

end AtlasKernelReplay

namespace Atlas

def seeds : Array Name := #[
  ``Nat.exists_infinite_primes,
  ``Nat.Prime.dvd_mul,
  ``Nat.Prime.dvd_of_dvd_pow,
  ``Nat.gcd_comm,
  ``Nat.gcd_dvd_left,
  ``Nat.gcd_dvd_right,
  ``Nat.coprime_iff_gcd_eq_one,
  ``Nat.Coprime.dvd_mul_right,
  ``Nat.ModEq.refl,
  ``Nat.ModEq.trans,
  ``Nat.ModEq.add,
  ``Nat.ModEq.mul,
  ``Set.ext,
  ``Set.union_comm,
  ``Set.inter_union_distrib_left,
  ``mul_inv_rev,
  ``map_inv,
  ``add_sq,
  ``LinearMap.finrank_range_add_finrank_ker,
  ``LinearMap.injective_iff_surjective,
  ``LinearMap.ker_eq_bot,
  ``intermediate_value_Icc,
  ``exists_deriv_eq_slope,
  ``Real.hasDerivAt_exp,
  ``Continuous.comp,
  ``IsCompact.image,
  ``IsConnected.image,
  ``PMF.tsum_coe,
  ``PMF.coe_le_one,
  ``PMF.apply_eq_one_iff,
  ``Finset.card_powerset,
  ``Finset.card_product,
  ``Finset.card_union_add_card_inter
]

def namesJson (ns : Array Name) : Json :=
  toJson <| (ns.qsort fun a b => a.toString < b.toString).map Name.toString

def orderedNamesJson (ns : List Name) : Json := toJson (ns.map Name.toString)

def nameParts : Name → List Json
  | .anonymous => []
  | .str p s => nameParts p ++ [Json.arr #[toJson "str", toJson s]]
  | .num p n => nameParts p ++ [Json.arr #[toJson "num", toJson (toString n)]]

def levelJson : Level → Json
  | .zero => Json.arr #[toJson "zero"]
  | .succ u => Json.arr #[toJson "succ", levelJson u]
  | .max u v => Json.arr #[toJson "max", levelJson u, levelJson v]
  | .imax u v => Json.arr #[toJson "imax", levelJson u, levelJson v]
  | .param n => Json.arr #[toJson "param", toJson (nameParts n)]
  | .mvar n => Json.arr #[toJson "mvar", toJson (nameParts n.name)]

def binderString : BinderInfo → String
  | .default => "explicit"
  | .implicit => "implicit"
  | .strictImplicit => "strict_implicit"
  | .instImplicit => "instance"

structure Encoding where
  ids : Std.HashMap Expr Nat := {}
  records : Array Json := #[]

partial def encodeExpr (e : Expr) : StateM Encoding Nat := do
  if let some i := (← get).ids[e]? then return i
  if let .mdata _ b := e then return ← encodeExpr b
  let j ← match e with
    | .bvar i => pure <| Json.arr #[toJson "bvar", toJson i]
    | .fvar i => pure <| Json.arr #[toJson "fvar", toJson (nameParts i.name)]
    | .mvar i => pure <| Json.arr #[toJson "mvar", toJson (nameParts i.name)]
    | .sort u => pure <| Json.arr #[toJson "sort", levelJson u]
    | .const n us => pure <| Json.arr #[toJson "const", toJson n.toString, toJson (us.map levelJson)]
    | .app f a => do
      let f ← encodeExpr f; let a ← encodeExpr a
      pure <| Json.arr #[toJson "app", toJson f, toJson a]
    | .lam n t b bi => do
      let t ← encodeExpr t; let b ← encodeExpr b
      pure <| Json.arr #[toJson "lambda", toJson (nameParts n), toJson t, toJson b, toJson (binderString bi)]
    | .forallE n t b bi => do
      let t ← encodeExpr t; let b ← encodeExpr b
      pure <| Json.arr #[toJson "forall", toJson (nameParts n), toJson t, toJson b, toJson (binderString bi)]
    | .letE n t v b nonDep => do
      let t ← encodeExpr t; let v ← encodeExpr v; let b ← encodeExpr b
      pure <| Json.arr #[toJson "let", toJson (nameParts n), toJson t, toJson v, toJson b, toJson nonDep]
    | .lit (.natVal n) => pure <| Json.arr #[toJson "nat_literal", toJson (toString n)]
    | .lit (.strVal s) => pure <| Json.arr #[toJson "string_literal", toJson s]
    | .proj n i b => do
      let b ← encodeExpr b
      pure <| Json.arr #[toJson "projection", toJson n.toString, toJson i, toJson b]
    | .mdata _ b => do
      let b ← encodeExpr b
      pure <| Json.arr #[toJson "metadata_body", toJson b]
  let i := (← get).records.size
  modify fun s => { ids := s.ids.insert e i, records := s.records.push j }
  return i

def kind : ConstantInfo → String
  | .axiomInfo _ => "axiom"
  | .thmInfo _ => "theorem"
  | .defnInfo _ => "definition"
  | .opaqueInfo _ => "opaque"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"
  | .quotInfo _ => "quotient_primitive"

partial def projectionStructures (e : Expr) : NameSet :=
  match e with
  | .app f a => projectionStructures f ++ projectionStructures a
  | .lam _ t b _ | .forallE _ t b _ => projectionStructures t ++ projectionStructures b
  | .letE _ t v b _ => projectionStructures t ++ projectionStructures v ++ projectionStructures b
  | .proj n _ b => (projectionStructures b).insert n
  | .mdata _ b => projectionStructures b
  | _ => {}

def kernelDependencies (ci : ConstantInfo) : NameSet := Id.run do
  let mut ns := ci.getUsedConstantsAsSet ++ projectionStructures ci.type
  if let some v := ci.value? (allowOpaque := true) then
    ns := ns ++ projectionStructures v
  match ci with
  | .inductInfo v =>
    for n in v.all ++ v.ctors do ns := ns.insert n
  | .ctorInfo v => ns := ns.insert v.induct
  | .recInfo v =>
    for n in v.all do ns := ns.insert n
    for r in v.rules do
      ns := ns.insert r.ctor
      ns := ns ++ r.rhs.getUsedConstantsAsSet
      ns := ns ++ projectionStructures r.rhs
  | _ => pure ()
  return ns

def prettyExpr (e : Expr) : CommandElabM String :=
  liftTermElabM <| withOptions
    (fun o => o.setBool `pp.proofs true |>.setNat `pp.width 96
      |>.setNat `pp.maxDepth 10000 |>.setNat `pp.maxSteps 1000000) do
    return (← Meta.ppExpr e).pretty 96

def statementContext (e : Expr) : CommandElabM Json :=
  liftTermElabM <| Meta.forallTelescope e fun xs conclusion => do
    let mut binders := #[]
    for x in xs do
      let d ← x.fvarId!.getDecl
      let hypothesis ← Meta.isProp d.type
      binders := binders.push <| Json.mkObj [
        ("name", toJson d.userName.toString),
        ("type", toJson (← Meta.ppExpr d.type).pretty),
        ("kind", toJson (if hypothesis then "hypothesis" else "parameter")),
        ("binder", toJson (binderString d.binderInfo))]
    return Json.mkObj [("binders", Json.arr binders),
      ("conclusion", toJson (← Meta.ppExpr conclusion).pretty)]

def metadata (ci : ConstantInfo) : Json :=
  match ci with
  | .inductInfo v => Json.mkObj [
      ("all", orderedNamesJson v.all), ("constructors", orderedNamesJson v.ctors),
      ("num_params", toJson v.numParams), ("num_indices", toJson v.numIndices),
      ("num_nested", toJson v.numNested), ("recursive", toJson v.isRec),
      ("reflexive", toJson v.isReflexive)]
  | .ctorInfo v => Json.mkObj [
      ("inductive", toJson v.induct.toString), ("constructor_index", toJson v.cidx),
      ("num_params", toJson v.numParams), ("num_fields", toJson v.numFields)]
  | .recInfo v => Json.mkObj [
      ("all", orderedNamesJson v.all), ("num_params", toJson v.numParams),
      ("num_indices", toJson v.numIndices), ("num_motives", toJson v.numMotives),
      ("num_minors", toJson v.numMinors), ("k", toJson v.k)]
  | .defnInfo v => Json.mkObj [("reducibility_hints", match v.hints with
      | .opaque => Json.arr #[toJson "opaque"]
      | .abbrev => Json.arr #[toJson "abbrev"]
      | .regular h => Json.arr #[toJson "regular", toJson h.toNat])]
  | _ => Json.mkObj []

elab "export_atlas" : command => do
  let env ← getEnv
  let out := System.FilePath.mk ((← IO.getEnv "ATLAS_OUTPUT_DIR").getD "atlas-export")
  IO.FS.createDirAll out
  let mut todo := seeds
  let mut cs : Std.HashMap Name ConstantInfo := {}
  while !todo.isEmpty do
    let n := todo.back!
    todo := todo.pop
    if cs.contains n then continue
    let some ci := env.find? n | throwError "Unresolved declaration: {n}"
    if ci.isUnsafe || ci.isPartial then throwError "Unsafe or partial dependency: {n}"
    if ci.type.hasMVar || ci.type.hasFVar then throwError "Open declaration type: {n}"
    cs := cs.insert n ci
    for d in kernelDependencies ci do
      if !cs.contains d then todo := todo.push d
  IO.println s!"Replaying {cs.size} declarations into a fresh Lean kernel environment..."
  let started ← IO.monoMsNow
  let empty ← mkEmptyEnvironment 0
  let checked ← AtlasKernelReplay.replay cs empty.checked.get
  for (n, _) in cs.toList do
    unless (checked.find? n).isSome do throwError "Replay omitted {n}"
  let invalid : Declaration := .thmDecl {
    name := `Atlas.invalidProofControl, levelParams := [],
    type := mkConst ``False, value := mkConst ``Nat.zero }
  match checked.addDeclCore 0 invalid none with
  | .error (.declTypeMismatch ..) => pure ()
  | .error _ => throwError "Negative control failed for an unexpected reason"
  | .ok _ => throwError "Kernel accepted a deliberately invalid proof"
  let elapsed := (← IO.monoMsNow) - started
  IO.println s!"Kernel replay passed in {elapsed} ms. Exporting declarations..."
  let file ← IO.FS.Handle.mk (out / "declarations.ndjson") .write
  let mut encoder : Encoding := {}
  let sorted := cs.toList.toArray.qsort fun a b => a.1.toString < b.1.toString
  let mut done := 0
  for (n, ci) in sorted do
    let typeRefs := ci.type.getUsedConstants
    let value := ci.value? (allowOpaque := true)
    let valueRefs := value.map Expr.getUsedConstants |>.getD #[]
    let deps := kernelDependencies ci
    let extra := deps.toArray.filter fun d => !typeRefs.contains d && !valueRefs.contains d
    let moduleName := (env.getModuleIdxFor? n).map (fun i => env.header.moduleNames[i.toNat]!.toString) |>.getD ""
    let doc := (← findDocString? env n).getD ""
    let range ← findDeclarationRanges? n
    let typeText ← prettyExpr ci.type
    let context ← statementContext ci.type
    let proofText ← match value with
      | some v => do
        if v.hasMVar || v.hasFVar then throwError "Open declaration value: {n}"
        pure <| toJson (← prettyExpr v)
      | none => pure Json.null
    let (typeRoot, s) := (encodeExpr ci.type).run encoder
    encoder := s
    let mut valueRoot := Json.null
    if let some v := value then
      let (i, s) := (encodeExpr v).run encoder
      encoder := s
      valueRoot := toJson i
    let mut rules := #[]
    if let .recInfo v := ci then
      for r in v.rules do
        let (i, s) := (encodeExpr r.rhs).run encoder
        encoder := s
        rules := rules.push <| Json.mkObj [("constructor", toJson r.ctor.toString),
          ("num_fields", toJson r.nfields), ("expression", toJson i)]
    let ax ← collectAxioms n
    let record := Json.mkObj [
      ("name", toJson n.toString), ("name_parts", toJson (nameParts n)),
      ("kind", toJson (kind ci)), ("module", toJson moduleName),
      ("level_parameters", toJson (ci.levelParams.map nameParts)),
      ("statement", toJson typeText), ("value", proofText),
      ("context", context),
      ("docstring", toJson doc), ("source_range", match range with
        | none => Json.null
        | some r => Json.mkObj [("start_line", toJson r.range.pos.line),
          ("start_column", toJson r.range.pos.column),
          ("end_line", toJson r.range.endPos.line),
          ("end_column", toJson r.range.endPos.column)]),
      ("type_dependencies", namesJson typeRefs), ("value_dependencies", namesJson valueRefs),
      ("kernel_dependencies", namesJson extra), ("axioms", namesJson ax),
      ("type_expression", toJson typeRoot), ("value_expression", valueRoot),
      ("recursor_rules", Json.arr rules), ("kernel_metadata", metadata ci),
      ("unsafe", toJson false), ("partial", toJson false),
      ("kernel_rechecked", toJson true)]
    file.putStrLn record.compress
    done := done + 1
    if done % 500 == 0 then IO.println s!"Exported {done}/{cs.size} declarations"
  file.flush
  let expressions ← IO.FS.Handle.mk (out / "expressions.ndjson") .write
  for j in encoder.records do expressions.putStrLn j.compress
  expressions.flush
  let report := Json.mkObj [
    ("schema_version", toJson "1.0.0"), ("seeds", namesJson seeds),
    ("declaration_count", toJson cs.size), ("expression_count", toJson encoder.records.size),
    ("kernel_replay", toJson "passed"), ("kernel_trust_level", toJson (0 : Nat)),
    ("kernel_negative_control", toJson "invalid proof rejected with type mismatch"),
    ("kernel_replay_ms", toJson elapsed),
    ("kernel_replay_method", toJson "Kernel.Environment.addDeclCore, trust 0; constructors and recursors compared with kernel-generated declarations"),
    ("expression_metadata", toJson "Kernel-irrelevant Expr.mdata annotations omitted"),
    ("scope", toJson "Transitive type, value, and inductive/recursor dependency closure of selected seeds")]
  IO.FS.writeFile (out / "export-report.json") (report.pretty ++ "\n")
  IO.println s!"Export complete: {cs.size} declarations, {encoder.records.size} shared expressions"

end Atlas

export_atlas
