import Lean
namespace AtlasRegression

theorem demo (p : Prop) (h : p) : p ∧ True := by
  constructor
  assumption
  trivial

end AtlasRegression

#print axioms AtlasRegression.demo
