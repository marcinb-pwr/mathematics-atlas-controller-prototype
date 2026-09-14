/-
Copyright (c) 2015 Microsoft Corporation. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Leonardo de Moura, Jeremy Avigad, Mario Carneiro
-/
import Batteries.Data.Nat.Gcd
import Mathlib.Algebra.Group.Nat.Units
import Mathlib.Algebra.GroupWithZero.Nat
import Mathlib.Algebra.Prime.Defs
import Mathlib.Data.Nat.Sqrt
import Mathlib.Order.Basic

/-!
# Prime numbers

This file deals with prime numbers: natural numbers `p ≥ 2` whose only divisors are `p` and `1`.

## Important declarations

- `Nat.Prime`: the predicate that expresses that a natural number `p` is prime
- `Nat.Primes`: the subtype of natural numbers that are prime
- `Nat.minFac n`: the minimal prime factor of a natural number `n ≠ 1`
- `Nat.prime_iff`: `Nat.Prime` coincides with the general definition of `Prime`
- `Nat.irreducible_iff_nat_prime`: a non-unit natural number is
                                  only divisible by `1` iff it is prime

-/

assert_not_exists Ring

open Bool Subtype Nat

namespace Nat
variable {n : ℕ}

/-- `Nat.Prime p` means that `p` is a prime number, that is, a natural number
  at least 2 whose only divisors are `p` and `1`.
  The theorem `Nat.prime_def` witnesses this description of a prime number. -/
@[pp_nodot]
def Prime (p : ℕ) :=
  Irreducible p

theorem irreducible_iff_nat_prime (a : ℕ) : Irreducible a ↔ Nat.Prime a :=
  Iff.rfl

theorem not_prime_zero : ¬ Prime 0
  | h => h.ne_zero rfl

/-- A copy of `not_prime_zero` stated in a way that works for `aesop`.

See https://github.com/leanprover-community/aesop/issues/197 for an explanation. -/
@[aesop safe destruct] theorem prime_zero_false : Prime 0 → False :=
  not_prime_zero

theorem not_prime_one : ¬ Prime 1
  | h => h.ne_one rfl

/-- A copy of `not_prime_one` stated in a way that works for `aesop`.

See https://github.com/leanprover-community/aesop/issues/197 for an explanation. -/
@[aesop safe destruct] theorem prime_one_false : Prime 1 → False :=
  not_prime_one


theorem Prime.ne_zero {n : ℕ} (h : Prime n) : n ≠ 0 := by
  intro h_atlas_0
  apply Nat.prime_zero_false
  rw [← h_atlas_0]
  assumption

end Nat

#print axioms Nat.Prime.ne_zero
