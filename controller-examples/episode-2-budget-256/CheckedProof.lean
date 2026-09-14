/-
Copyright (c) 2015 Microsoft Corporation. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Leonardo de Moura, Jeremy Avigad, Minchao Wu, Mario Carneiro
-/
import Mathlib.Data.Finset.Attach
import Mathlib.Data.Finset.Disjoint
import Mathlib.Data.Finset.Erase
import Mathlib.Data.Finset.Filter
import Mathlib.Data.Finset.Range
import Mathlib.Data.Finset.SDiff
import Mathlib.Data.Multiset.Basic
import Mathlib.Logic.Equiv.Set
import Mathlib.Order.Directed
import Mathlib.Order.Interval.Set.Defs
import Mathlib.Data.Set.SymmDiff

/-!
# Basic lemmas on finite sets

This file contains lemmas on the interaction of various definitions on the `Finset` type.

For an explanation of `Finset` design decisions, please see `Mathlib/Data/Finset/Defs.lean`.

## Main declarations

### Main definitions

* `Finset.choose`: Given a proof `h` of existence and uniqueness of a certain element
  satisfying a predicate, `choose s h` returns the element of `s` satisfying that predicate.

### Equivalences between finsets

* The `Mathlib/Logic/Equiv/Defs.lean` file describes a general type of equivalence, so look in there
  for any lemmas. There is some API for rewriting sums and products from `s` to `t` given that
  `s ≃ t`.
  TODO: examples

## Tags

finite sets, finset

-/

-- Assert that we define `Finset` without the material on `List.sublists`.
-- Note that we cannot use `List.sublists` itself as that is defined very early.
assert_not_exists List.sublistsLen Multiset.powerset CompleteLattice Monoid

open Multiset Subtype Function

universe u

variable {α : Type*} {β : Type*} {γ : Type*}

namespace Finset

-- TODO: these should be global attributes, but this will require fixing other files
attribute [local trans] Subset.trans Superset.trans

set_option linter.deprecated false in
@[deprecated "Deprecated without replacement." (since := "2025-02-07")]
theorem sizeOf_lt_sizeOf_of_mem [SizeOf α] {x : α} {s : Finset α} (hx : x ∈ s) :
    SizeOf.sizeOf x < SizeOf.sizeOf s := by
  cases s
  dsimp [SizeOf.sizeOf, SizeOf.sizeOf, Multiset.sizeOf]
  rw [Nat.add_comm]
  refine lt_trans ?_ (Nat.lt_succ_self _)
  exact Multiset.sizeOf_lt_sizeOf_of_mem hx

/-! ### Lattice structure -/

section Lattice

variable [DecidableEq α] {s s₁ s₂ t t₁ t₂ u v : Finset α} {a b : α}

/-! #### union -/

@[simp]

theorem disjUnion_eq_union (s t h) : @disjUnion α s t h = s ∪ t := by
  apply Finset.ext
  intro h_atlas_0
  rw [Finset.mem_disjUnion]
  rw [Finset.mem_union]

end Lattice
end Finset

#print axioms Finset.disjUnion_eq_union
