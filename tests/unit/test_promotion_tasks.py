"""Unit tests for Ring Promotion Celery Tasks"""

import unittest
from unittest.mock import Mock, patch, MagicMock
from celery.exceptions import Retry

from autopackager.orchestration.tasks import check_ring_promotions, deployment_task


class TestDeploymentChainingGuard(unittest.TestCase):
    """deployment_task must NOT deploy when testing didn't pass (else it loops
    on 'package has not passed testing' — the job-21 RealPlayer incident)."""

    @patch('autopackager.agents.deployment.DeploymentAgent')
    def test_skips_deploy_when_validation_failed(self, mock_agent_cls):
        prev = {"job_id": 1, "test_passed": False, "validation_failed": True,
                "needs_engineer_review": True}
        result = deployment_task(prev, 1)
        self.assertEqual(result, prev)
        mock_agent_cls.assert_not_called()

    @patch('autopackager.agents.deployment.DeploymentAgent')
    def test_skips_deploy_when_test_not_passed(self, mock_agent_cls):
        prev = {"job_id": 2, "test_passed": False}
        result = deployment_task(prev, 2)
        self.assertEqual(result, prev)
        mock_agent_cls.assert_not_called()


class TestCheckRingPromotionsTask(unittest.TestCase):
    """Test cases for check_ring_promotions Celery task"""

    def setUp(self):
        """Set up test fixtures"""
        # Mock task context (bound task)
        self.mock_task = Mock()
        self.mock_task.retry = Mock(side_effect=Retry)

    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.tasks.logger')
    def test_check_ring_promotions_success(self, mock_logger, mock_deployment_agent_class):
        """Test successful ring promotion check"""
        # Mock DeploymentAgent instance
        mock_agent = Mock()
        mock_deployment_agent_class.return_value = mock_agent

        # Mock successful result from check_and_promote_eligible_deployments
        mock_result = {
            'total_checked': 5,
            'eligible_count': 2,
            'promoted_count': 2,
            'failed_promotions': 0,
            'errors': [],
            'promotions': [
                {'deployment_id': 1, 'from_ring': 'IT Pilot', 'to_ring': 'Early Adopters'},
                {'deployment_id': 2, 'from_ring': 'Early Adopters', 'to_ring': 'Broad Deployment'}
            ]
        }
        mock_agent.check_and_promote_eligible_deployments.return_value = mock_result

        # Execute task
        result = check_ring_promotions()

        # Verify DeploymentAgent was instantiated
        mock_deployment_agent_class.assert_called_once()

        # Verify check_and_promote_eligible_deployments was called
        mock_agent.check_and_promote_eligible_deployments.assert_called_once()

        # Verify result
        self.assertEqual(result, mock_result)
        self.assertEqual(result['total_checked'], 5)
        self.assertEqual(result['promoted_count'], 2)

        # Verify logging
        mock_logger.info.assert_any_call("Starting ring promotion check")
        mock_logger.info.assert_any_call(
            "Ring promotion check completed",
            total_checked=5,
            eligible_count=2,
            promoted_count=2,
            failed_promotions=0
        )

    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.tasks.logger')
    def test_check_ring_promotions_no_eligible_deployments(self, mock_logger, mock_deployment_agent_class):
        """Test ring promotion check when no deployments are eligible"""
        # Mock DeploymentAgent instance
        mock_agent = Mock()
        mock_deployment_agent_class.return_value = mock_agent

        # Mock result with no promotions
        mock_result = {
            'total_checked': 3,
            'eligible_count': 0,
            'promoted_count': 0,
            'failed_promotions': 0,
            'errors': [],
            'promotions': []
        }
        mock_agent.check_and_promote_eligible_deployments.return_value = mock_result

        # Execute task
        result = check_ring_promotions()

        # Verify result
        self.assertEqual(result['promoted_count'], 0)
        self.assertEqual(result['total_checked'], 3)
        self.assertIsInstance(result['promotions'], list)
        self.assertEqual(len(result['promotions']), 0)

        # Verify logging shows zero promotions
        mock_logger.info.assert_any_call(
            "Ring promotion check completed",
            total_checked=3,
            eligible_count=0,
            promoted_count=0,
            failed_promotions=0
        )

    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.tasks.logger')
    def test_check_ring_promotions_handles_missing_result_keys(self, mock_logger, mock_deployment_agent_class):
        """Test task handles missing keys in result dictionary gracefully"""
        # Mock DeploymentAgent instance
        mock_agent = Mock()
        mock_deployment_agent_class.return_value = mock_agent

        # Mock incomplete result (missing some keys)
        mock_result = {
            'total_checked': 2,
            'promoted_count': 1
            # Missing eligible_count and failed_promotions
        }
        mock_agent.check_and_promote_eligible_deployments.return_value = mock_result

        # Execute task
        result = check_ring_promotions()

        # Verify result
        self.assertEqual(result['total_checked'], 2)
        self.assertEqual(result['promoted_count'], 1)

        # Verify logging uses .get() with defaults for missing keys
        mock_logger.info.assert_any_call(
            "Ring promotion check completed",
            total_checked=2,
            eligible_count=0,      # Default value
            promoted_count=1,
            failed_promotions=0    # Default value
        )

    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.tasks.logger')
    def test_check_ring_promotions_agent_raises_exception(self, mock_logger, mock_deployment_agent_class):
        """Test task handles exceptions from DeploymentAgent and retries"""
        # Mock DeploymentAgent instance
        mock_agent = Mock()
        mock_deployment_agent_class.return_value = mock_agent

        # Mock exception from check_and_promote_eligible_deployments
        test_error = Exception("Database connection failed")
        mock_agent.check_and_promote_eligible_deployments.side_effect = test_error

        # Execute task with mock retry
        with patch.object(check_ring_promotions, 'retry', side_effect=Retry) as mock_retry:
            with self.assertRaises(Retry):
                check_ring_promotions()

            # Verify error was logged
            mock_logger.error.assert_called_once_with(
                "Ring promotion check failed",
                error="Database connection failed"
            )

            # Verify retry was called with correct parameters
            mock_retry.assert_called_once_with(
                exc=test_error,
                countdown=300,  # 5 minutes
                max_retries=3
            )

    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.tasks.logger')
    def test_check_ring_promotions_agent_instantiation_fails(self, mock_logger, mock_deployment_agent_class):
        """Test task handles DeploymentAgent instantiation failure"""
        # Mock exception during instantiation
        test_error = Exception("Configuration error")
        mock_deployment_agent_class.side_effect = test_error

        # Execute task with mock retry
        with patch.object(check_ring_promotions, 'retry', side_effect=Retry) as mock_retry:
            with self.assertRaises(Retry):
                check_ring_promotions()

            # Verify error was logged
            mock_logger.error.assert_called_once_with(
                "Ring promotion check failed",
                error="Configuration error"
            )

            # Verify retry was called
            mock_retry.assert_called_once()

    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.tasks.logger')
    def test_check_ring_promotions_multiple_promotions(self, mock_logger, mock_deployment_agent_class):
        """Test task logs correctly when multiple promotions occur"""
        # Mock DeploymentAgent instance
        mock_agent = Mock()
        mock_deployment_agent_class.return_value = mock_agent

        # Mock result with multiple promotions
        mock_result = {
            'total_checked': 10,
            'eligible_count': 5,
            'promoted_count': 5,
            'failed_promotions': 0,
            'errors': [],
            'promotions': [
                {'deployment_id': i, 'from_ring': 'IT Pilot', 'to_ring': 'Early Adopters'}
                for i in range(1, 6)
            ]
        }
        mock_agent.check_and_promote_eligible_deployments.return_value = mock_result

        # Execute task
        result = check_ring_promotions()

        # Verify all promotions are recorded
        self.assertEqual(result['promoted_count'], 5)
        self.assertEqual(len(result['promotions']), 5)

        # Verify logging
        mock_logger.info.assert_any_call(
            "Ring promotion check completed",
            total_checked=10,
            eligible_count=5,
            promoted_count=5,
            failed_promotions=0
        )

    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.tasks.logger')
    def test_check_ring_promotions_all_deployments_blocked(self, mock_logger, mock_deployment_agent_class):
        """Test task when all deployments are blocked from promotion"""
        # Mock DeploymentAgent instance
        mock_agent = Mock()
        mock_deployment_agent_class.return_value = mock_agent

        # Mock result with nothing eligible (all blocked / dwell time not met)
        mock_result = {
            'total_checked': 4,
            'eligible_count': 0,
            'promoted_count': 0,
            'failed_promotions': 0,
            'errors': [],
            'promotions': []
        }
        mock_agent.check_and_promote_eligible_deployments.return_value = mock_result

        # Execute task
        result = check_ring_promotions()

        # Verify result
        self.assertEqual(result['promoted_count'], 0)
        self.assertEqual(result['eligible_count'], 0)

        # Verify logging
        mock_logger.info.assert_any_call(
            "Ring promotion check completed",
            total_checked=4,
            eligible_count=0,
            promoted_count=0,
            failed_promotions=0
        )

    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.tasks.logger')
    def test_check_ring_promotions_returns_dict(self, mock_logger, mock_deployment_agent_class):
        """Test that task always returns a dictionary"""
        # Mock DeploymentAgent instance
        mock_agent = Mock()
        mock_deployment_agent_class.return_value = mock_agent

        # Mock result
        mock_result = {
            'total_checked': 1,
            'eligible_count': 1,
            'promoted_count': 1,
            'failed_promotions': 0,
            'errors': [],
            'promotions': []
        }
        mock_agent.check_and_promote_eligible_deployments.return_value = mock_result

        # Execute task
        result = check_ring_promotions()

        # Verify result is a dictionary
        self.assertIsInstance(result, dict)
        self.assertIn('total_checked', result)
        self.assertIn('promoted_count', result)

    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.tasks.logger')
    def test_check_ring_promotions_retry_countdown(self, mock_logger, mock_deployment_agent_class):
        """Test that retry uses correct countdown value (5 minutes = 300 seconds)"""
        # Mock DeploymentAgent instance
        mock_agent = Mock()
        mock_deployment_agent_class.return_value = mock_agent

        # Mock exception
        test_error = Exception("Temporary failure")
        mock_agent.check_and_promote_eligible_deployments.side_effect = test_error

        # Execute task with mock retry
        with patch.object(check_ring_promotions, 'retry', side_effect=Retry) as mock_retry:
            with self.assertRaises(Retry):
                check_ring_promotions()

            # Verify retry countdown is 5 minutes (300 seconds)
            call_kwargs = mock_retry.call_args[1]
            self.assertEqual(call_kwargs['countdown'], 300)

    @patch('autopackager.agents.deployment.DeploymentAgent')
    @patch('autopackager.orchestration.tasks.logger')
    def test_check_ring_promotions_max_retries(self, mock_logger, mock_deployment_agent_class):
        """Test that retry uses correct max_retries value"""
        # Mock DeploymentAgent instance
        mock_agent = Mock()
        mock_deployment_agent_class.return_value = mock_agent

        # Mock exception
        test_error = Exception("Persistent failure")
        mock_agent.check_and_promote_eligible_deployments.side_effect = test_error

        # Execute task with mock retry
        with patch.object(check_ring_promotions, 'retry', side_effect=Retry) as mock_retry:
            with self.assertRaises(Retry):
                check_ring_promotions()

            # Verify max_retries is 3
            call_kwargs = mock_retry.call_args[1]
            self.assertEqual(call_kwargs['max_retries'], 3)


if __name__ == '__main__':
    unittest.main()
