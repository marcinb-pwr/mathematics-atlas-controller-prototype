import AtlasSession
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


theorem irreducible_iff_nat_prime (a : ℕ) : Irreducible a ↔ Nat.Prime a := by
  atlas_session

end Nat
