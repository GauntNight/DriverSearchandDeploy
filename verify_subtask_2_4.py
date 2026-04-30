#!/usr/bin/env python3
"""Verification script for subtask-2-4: check_and_promote_eligible_deployments method"""

import sys
import inspect

try:
    from autopackager.agents.deployment.deployment_agent import DeploymentAgent

    # Create agent instance
    agent = DeploymentAgent()

    # Check that method exists
    assert hasattr(agent, 'check_and_promote_eligible_deployments'), \
        "Method check_and_promote_eligible_deployments not found"

    # Verify it's callable
    assert callable(agent.check_and_promote_eligible_deployments), \
        "check_and_promote_eligible_deployments is not callable"

    # Check method signature
    method = getattr(agent, 'check_and_promote_eligible_deployments')
    sig = inspect.signature(method)

    # Verify return type annotation
    assert sig.return_annotation != inspect.Signature.empty, \
        "Method should have return type annotation"

    # Verify docstring exists
    assert method.__doc__ is not None and len(method.__doc__) > 50, \
        "Method should have a comprehensive docstring"

    # Check that it follows the pattern of returning a Dict
    assert 'Dict' in str(sig.return_annotation), \
        "Method should return Dict[str, Any]"

    print("OK")
    print("\n✓ Method check_and_promote_eligible_deployments exists")
    print("✓ Method is callable")
    print("✓ Method has proper type annotations")
    print("✓ Method has comprehensive docstring")
    print("\nAll verifications passed!")
    sys.exit(0)

except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
