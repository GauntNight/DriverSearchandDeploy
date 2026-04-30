"""Integration tests for automated ring promotion

This module tests the end-to-end flow of automated ring promotion including:
- Deployment eligibility checks
- Automatic promotion from Ring 0 to Ring 1
- Batch promotion processing
- Celery task integration
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, call
from datetime import datetime, timedelta

from autopackager.models.deployment import Deployment, DeploymentStatus
from autopackager.agents.deployment.deployment_agent import DeploymentAgent
from autopackager.orchestration.tasks import check_ring_promotions


# Mark all tests in this module as integration tests
pytestmark = pytest.mark.integration


class TestAutomatedRingPromotion:
    """Integration tests for end-to-end ring promotion flow"""

    @pytest.fixture
    def mock_config(self):
        """Mock configuration with ring promotion settings"""
        return {
            'ring_promotion': {
                'enabled': True,
                'success_threshold_percent': 90.0,
                'minimum_install_count': 10,
                'evaluation_period_hours': 48,
                'auto_promote': True
            },
            'deployment_rings': [
                {
                    'name': 'IT Pilot',
                    'ring_id': 'ring0',
                    'entra_group_id': 'group-ring0-id',
                    'deferral_days': 0
                },
                {
                    'name': 'Early Adopters',
                    'ring_id': 'ring1',
                    'entra_group_id': 'group-ring1-id',
                    'deferral_days': 3
                },
                {
                    'name': 'Broad Deployment',
                    'ring_id': 'ring2',
                    'entra_group_id': 'group-ring2-id',
                    'deferral_days': 7
                },
                {
                    'name': 'Critical Systems',
                    'ring_id': 'ring3',
                    'entra_group_id': 'group-ring3-id',
                    'deferral_days': 14
                }
            ]
        }

    @patch('autopackager.agents.deployment.deployment_agent.GraphAPIClient')
    @patch('autopackager.agents.deployment.deployment_agent.db_session_scope')
    @patch('autopackager.agents.deployment.deployment_agent.get_config')
    def test_e2e_automated_promotion_ring0_to_ring1(
        self,
        mock_get_config,
        mock_db_session_scope,
        mock_graph_client_class,
        mock_config
    ):
        """
        End-to-end test: Deploy to Ring 0, verify automatic promotion to Ring 1

        Steps:
        1. Deploy package to Ring 0 (IT Pilot) with 95% success rate
        2. Fast-forward time past 48h dwell period
        3. Check deployment eligibility for promotion
        4. Promote deployment to Ring 1
        5. Verify promotion timestamp and metrics recorded
        """
        # Setup mocks
        mock_get_config.return_value = mock_config

        # Create mock deployment (Ring 0, deployed 50 hours ago)
        deployed_at = datetime.utcnow() - timedelta(hours=50)
        mock_deployment = MagicMock(spec=Deployment)
        mock_deployment.id = 1
        mock_deployment.package_id = 123
        mock_deployment.intune_app_id = 'intune-app-123'
        mock_deployment.ring_id = 'ring0'
        mock_deployment.ring_name = 'IT Pilot'
        mock_deployment.status = DeploymentStatus.IN_PROGRESS
        mock_deployment.target_device_count = 20
        mock_deployment.successful_installs = 19  # 95% success rate
        mock_deployment.failed_installs = 1
        mock_deployment.deployed_at = deployed_at
        mock_deployment.promoted_at = None
        mock_deployment.promotion_blocked_reason = None

        # Mock database session
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = mock_deployment
        mock_db_session_scope.return_value.__enter__.return_value = mock_session

        # Mock Graph API client
        mock_graph_client = MagicMock()
        mock_graph_client.assign_app_to_group.return_value = 'assignment-ring1-new'
        mock_graph_client_class.return_value = mock_graph_client

        # Create deployment agent
        agent = DeploymentAgent()
        agent.config = mock_config

        # Calculate expected success rate: 19/20 = 95%
        success_rate = (mock_deployment.successful_installs / mock_deployment.target_device_count) * 100
        assert success_rate == 95.0

        # Step 1: Check eligibility
        is_eligible, reason = agent.is_eligible_for_promotion(mock_deployment)
        assert is_eligible is True, f"Deployment should be eligible but got: {reason}"
        assert "95.0%" in reason

        # Step 2: Promote deployment
        result = agent.promote_to_next_ring(mock_deployment.id)

        # Step 3: Verify promotion occurred
        assert result is not None
        assert result['from_ring'] == 'IT Pilot'
        assert result['to_ring'] == 'Early Adopters'
        assert result['package_id'] == 123
        assert result['intune_app_id'] == 'intune-app-123'

        # Step 4: Verify Graph API was called to create Ring 1 assignment
        # Note: assign_app_to_group uses positional args: (app_id, group_id, intent='required')
        mock_graph_client.assign_app_to_group.assert_called_once_with(
            'intune-app-123',
            'group-ring1-id',
            intent='required'
        )

    @patch('autopackager.agents.deployment.deployment_agent.get_config')
    def test_e2e_promotion_blocked_by_low_success_rate(
        self,
        mock_get_config,
        mock_config
    ):
        """
        End-to-end test: Verify promotion blocked when success rate < 90%
        """
        # Setup mocks
        mock_get_config.return_value = mock_config

        # Create deployment with 85% success rate (below 90% threshold)
        deployed_at = datetime.utcnow() - timedelta(hours=50)
        mock_deployment = MagicMock(spec=Deployment)
        mock_deployment.id = 2
        mock_deployment.ring_id = 'ring0'
        mock_deployment.ring_name = 'IT Pilot'
        mock_deployment.status = DeploymentStatus.IN_PROGRESS
        mock_deployment.target_device_count = 20
        mock_deployment.successful_installs = 17  # 85% success rate
        mock_deployment.failed_installs = 3
        mock_deployment.deployed_at = deployed_at
        mock_deployment.promotion_blocked_reason = None

        # Create deployment agent
        agent = DeploymentAgent()
        agent.config = mock_config

        # Check eligibility - should be blocked
        is_eligible, reason = agent.is_eligible_for_promotion(mock_deployment)

        assert is_eligible is False
        assert 'success rate' in reason.lower()
        assert '85.0%' in reason

    @patch('autopackager.agents.deployment.deployment_agent.get_config')
    def test_e2e_batch_promotion_check(
        self,
        mock_get_config,
        mock_config
    ):
        """
        End-to-end test: Verify eligibility checking logic with multiple deployment states

        Tests that the is_eligible_for_promotion method correctly evaluates different scenarios:
        - Eligible: 95% success, 50h dwell time → Should be eligible
        - Not eligible: 80% success rate → Should fail on success threshold
        - Not eligible: 24h dwell time → Should fail on dwell time
        - Not eligible: manually blocked → Should fail on manual block
        """
        # Setup mocks
        mock_get_config.return_value = mock_config

        # Create deployment agent
        agent = DeploymentAgent()
        agent.config = mock_config

        # Create mock deployments with different states
        deployed_at_eligible = datetime.utcnow() - timedelta(hours=50)
        deployed_at_recent = datetime.utcnow() - timedelta(hours=24)

        # Test 1: Eligible deployment (95% success, 50h dwell time)
        deployment1 = MagicMock(spec=Deployment)
        deployment1.id = 1
        deployment1.ring_id = 'ring0'
        deployment1.ring_name = 'IT Pilot'
        deployment1.status = DeploymentStatus.IN_PROGRESS
        deployment1.target_device_count = 20
        deployment1.successful_installs = 19
        deployment1.failed_installs = 1
        deployment1.deployed_at = deployed_at_eligible
        deployment1.promotion_blocked_reason = None

        is_eligible, reason = agent.is_eligible_for_promotion(deployment1)
        assert is_eligible is True, f"Expected eligible but got: {reason}"

        # Test 2: Not eligible (80% success rate - below 90% threshold)
        deployment2 = MagicMock(spec=Deployment)
        deployment2.id = 2
        deployment2.ring_id = 'ring0'
        deployment2.status = DeploymentStatus.IN_PROGRESS
        deployment2.target_device_count = 20
        deployment2.successful_installs = 16  # 80%
        deployment2.failed_installs = 4
        deployment2.deployed_at = deployed_at_eligible
        deployment2.promotion_blocked_reason = None

        is_eligible, reason = agent.is_eligible_for_promotion(deployment2)
        assert is_eligible is False
        assert 'success rate' in reason.lower()

        # Test 3: Not eligible (only 24h dwell time - needs 48h)
        deployment3 = MagicMock(spec=Deployment)
        deployment3.id = 3
        deployment3.ring_id = 'ring0'
        deployment3.status = DeploymentStatus.IN_PROGRESS
        deployment3.target_device_count = 20
        deployment3.successful_installs = 19
        deployment3.failed_installs = 1
        deployment3.deployed_at = deployed_at_recent
        deployment3.promotion_blocked_reason = None

        is_eligible, reason = agent.is_eligible_for_promotion(deployment3)
        assert is_eligible is False
        assert 'hours remaining' in reason.lower()

        # Test 4: Manually blocked
        deployment4 = MagicMock(spec=Deployment)
        deployment4.id = 4
        deployment4.ring_id = 'ring0'
        deployment4.status = DeploymentStatus.IN_PROGRESS
        deployment4.target_device_count = 20
        deployment4.successful_installs = 19
        deployment4.failed_installs = 1
        deployment4.deployed_at = deployed_at_eligible
        deployment4.promotion_blocked_reason = 'Testing issues found'

        is_eligible, reason = agent.is_eligible_for_promotion(deployment4)
        assert is_eligible is False
        assert 'manually blocked' in reason.lower()

    @patch('autopackager.agents.deployment.DeploymentAgent')
    def test_e2e_celery_task_integration(self, mock_agent_class):
        """
        End-to-end test: Verify Celery task calls promotion logic correctly

        Mocks the DeploymentAgent to avoid database connections and verifies
        that the Celery task correctly calls the promotion logic and returns results.
        """
        # Setup mock agent
        mock_agent = MagicMock()
        mock_agent.check_and_promote_eligible_deployments.return_value = {
            'total_checked': 5,
            'eligible_count': 3,
            'promoted_count': 2,
            'failed_promotions': 0,
            'errors': [],
            'promotions': [
                {
                    'deployment_id': 1,
                    'from_ring': 'IT Pilot',
                    'to_ring': 'Early Adopters',
                    'package_id': 101,
                    'intune_app_id': 'app-1'
                },
                {
                    'deployment_id': 2,
                    'from_ring': 'IT Pilot',
                    'to_ring': 'Early Adopters',
                    'package_id': 102,
                    'intune_app_id': 'app-2'
                }
            ]
        }
        mock_agent_class.return_value = mock_agent

        # Execute Celery task
        result = check_ring_promotions.apply().get()

        # Verify agent was created and method was called
        mock_agent_class.assert_called_once()
        mock_agent.check_and_promote_eligible_deployments.assert_called_once()

        # Verify result structure matches expected keys
        assert result['total_checked'] == 5
        assert result['eligible_count'] == 3
        assert result['promoted_count'] == 2
        assert result['failed_promotions'] == 0
        assert len(result['promotions']) == 2
