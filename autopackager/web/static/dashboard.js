/**
 * AutoPackager Dashboard JavaScript Module
 * Handles API data fetching, rendering, and auto-refresh functionality
 */

class DashboardApp {
    constructor() {
        this.apiBase = '/api';
        this.refreshInterval = 5000; // 5 seconds
        this.refreshTimer = null;
        this.isRefreshEnabled = true;

        // Initialize the dashboard
        this.init();
    }

    /**
     * Initialize dashboard and set up event listeners
     */
    init() {
        // Load initial data
        this.loadAllData();

        // Set up event listeners
        this.setupEventListeners();

        // Start auto-refresh
        this.startAutoRefresh();
    }

    /**
     * Set up event listeners for filters and controls
     */
    setupEventListeners() {
        // Job state filter
        const jobFilter = document.getElementById('job-filter');
        if (jobFilter) {
            jobFilter.addEventListener('change', () => {
                this.loadJobs(jobFilter.value);
            });
        }

        // Activity limit filter
        const activityLimit = document.getElementById('activity-limit');
        if (activityLimit) {
            activityLimit.addEventListener('change', () => {
                this.loadActivity(parseInt(activityLimit.value));
            });
        }
    }

    /**
     * Load all dashboard data
     */
    async loadAllData() {
        try {
            await Promise.all([
                this.loadStatistics(),
                this.loadJobs(),
                this.loadDeploymentRings(),
                this.loadActivity()
            ]);

            this.updateLastRefreshTime();
        } catch (error) {
            console.error('Error loading dashboard data:', error);
            this.showError('Failed to load dashboard data');
        }
    }

    /**
     * Load statistics overview
     */
    async loadStatistics() {
        try {
            const data = await this.fetchAPI('/stats');

            // Update job statistics
            this.updateElement('stat-jobs-total', data.jobs.total);
            this.updateElement('stat-jobs-pending', data.jobs.pending);
            this.updateElement('stat-jobs-inprogress',
                data.jobs.discovering + data.jobs.packaging + data.jobs.testing + data.jobs.deploying);
            this.updateElement('stat-jobs-completed', data.jobs.completed);
            this.updateElement('stat-jobs-failed', data.jobs.failed);
            this.updateElement('stat-jobs-recent', data.jobs.recent_24h);

            // Update deployment statistics
            this.updateElement('stat-deployments-total', data.deployments.total);
            this.updateElement('stat-deployments-successful', data.deployments.successful);
            this.updateElement('stat-deployments-failed', data.deployments.failed);
            this.updateElement('stat-deployments-inprogress', data.deployments.in_progress);
            this.updateElement('stat-deployments-recent', data.deployments.recent_24h);

            // Update package statistics
            this.updateElement('stat-packages-total', data.packages.total);
            this.updateElement('stat-packages-tested', data.packages.tested);
            this.updateElement('stat-packages-deployed', data.packages.deployed);

        } catch (error) {
            console.error('Error loading statistics:', error);
        }
    }

    /**
     * Load jobs list
     */
    async loadJobs(state = '') {
        try {
            const url = state ? `/jobs?state=${state}&limit=50` : '/jobs?limit=50';
            const data = await this.fetchAPI(url);

            const jobsList = document.getElementById('jobs-list');
            if (!jobsList) return;

            if (!data.jobs || data.jobs.length === 0) {
                jobsList.innerHTML = '<div class="loading">No jobs found</div>';
                return;
            }

            // Render job cards
            jobsList.innerHTML = data.jobs.map(job => this.renderJobCard(job)).join('');

        } catch (error) {
            console.error('Error loading jobs:', error);
            const jobsList = document.getElementById('jobs-list');
            if (jobsList) {
                jobsList.innerHTML = '<div class="loading">Error loading jobs</div>';
            }
        }
    }

    /**
     * Render a single job card
     */
    renderJobCard(job) {
        const createdAt = this.formatDateTime(job.created_at);
        const updatedAt = this.formatDateTime(job.updated_at);
        const statusCategory = this.getJobStatusCategory(job.state);
        const statusDisplay = this.getJobStatusDisplay(job.state);
        const statusIcon = this.getJobStatusIcon(statusCategory);

        return `
            <div class="job-card state-${job.state}">
                <div class="job-header">
                    <div class="job-id">${statusIcon} Job #${job.id}</div>
                    <div class="job-state state-${job.state}">${statusDisplay}</div>
                </div>
                <div class="job-details">
                    <div><strong>Software:</strong> ${this.escapeHtml(job.software_title || 'N/A')}</div>
                    <div><strong>Vendor:</strong> ${this.escapeHtml(job.vendor || 'N/A')} | <strong>Model:</strong> ${this.escapeHtml(job.hardware_model || 'N/A')}</div>
                    ${job.driver_type ? `<div><strong>Driver Type:</strong> ${this.escapeHtml(job.driver_type)}</div>` : ''}
                    <div><strong>Created:</strong> ${createdAt}</div>
                    ${statusCategory !== 'pending' ? `<div><strong>Updated:</strong> ${updatedAt}</div>` : ''}
                    ${job.error_message ? `<div class="text-danger" style="margin-top: 0.5rem;"><strong>Error:</strong> ${this.escapeHtml(job.error_message)}</div>` : ''}
                </div>
            </div>
        `;
    }

    /**
     * Get job status category (pending, in-progress, completed, failed)
     */
    getJobStatusCategory(state) {
        const inProgressStates = ['discovering', 'packaging', 'testing', 'deploying'];

        if (state === 'pending') {
            return 'pending';
        } else if (inProgressStates.includes(state)) {
            return 'in-progress';
        } else if (state === 'completed') {
            return 'completed';
        } else if (state === 'failed' || state === 'cancelled') {
            return 'failed';
        }
        return 'unknown';
    }

    /**
     * Get user-friendly display name for job state
     */
    getJobStatusDisplay(state) {
        const stateMap = {
            'pending': 'Pending',
            'discovering': 'Discovering',
            'packaging': 'Packaging',
            'testing': 'Testing',
            'deploying': 'Deploying',
            'completed': 'Completed',
            'failed': 'Failed',
            'cancelled': 'Cancelled'
        };
        return stateMap[state] || state.charAt(0).toUpperCase() + state.slice(1);
    }

    /**
     * Get icon for job status category
     */
    getJobStatusIcon(category) {
        const iconMap = {
            'pending': '⏳',
            'in-progress': '⚙️',
            'completed': '✅',
            'failed': '❌',
            'unknown': '❓'
        };
        return iconMap[category] || '';
    }

    /**
     * Load deployment rings
     */
    async loadDeploymentRings() {
        try {
            const data = await this.fetchAPI('/deployments/rings');

            const ringsList = document.getElementById('rings-list');
            if (!ringsList) return;

            if (!data.rings || data.rings.length === 0) {
                ringsList.innerHTML = '<div class="loading">No deployment rings data available</div>';
                return;
            }

            // Render ring cards
            ringsList.innerHTML = data.rings.map(ring => this.renderRingCard(ring)).join('');

        } catch (error) {
            console.error('Error loading deployment rings:', error);
            const ringsList = document.getElementById('rings-list');
            if (ringsList) {
                ringsList.innerHTML = '<div class="loading">Error loading deployment rings</div>';
            }
        }
    }

    /**
     * Render a single ring card
     */
    renderRingCard(ring) {
        const total = ring.total_deployments;
        const successful = ring.successful || 0;
        const failed = ring.failed || 0;
        const inProgress = ring.in_progress || 0;
        const pending = ring.pending || 0;

        // Calculate progress percentage (successful out of completed deployments)
        const completed = successful + failed;
        const progressPercentage = completed > 0 ? (successful / completed) * 100 : 0;

        return `
            <div class="ring-card">
                <div class="ring-header">
                    <div class="ring-name">${this.escapeHtml(ring.ring_name)}</div>
                    <div class="ring-stats">
                        <div class="ring-stat success">✓ ${successful}</div>
                        <div class="ring-stat failed">✗ ${failed}</div>
                    </div>
                </div>
                <div class="ring-progress">
                    <div class="ring-progress-bar" style="width: ${progressPercentage}%"></div>
                </div>
                <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.5rem;">
                    Total: ${total} | In Progress: ${inProgress} | Pending: ${pending}
                </div>
            </div>
        `;
    }

    /**
     * Load recent activity timeline
     */
    async loadActivity(limit = 50) {
        try {
            const data = await this.fetchAPI(`/activity?limit=${limit}`);

            const timeline = document.getElementById('activity-timeline');
            if (!timeline) return;

            if (!data.activity || data.activity.length === 0) {
                timeline.innerHTML = '<div class="loading">No recent activity</div>';
                return;
            }

            // Render activity items
            timeline.innerHTML = data.activity.map(item => this.renderActivityItem(item)).join('');

        } catch (error) {
            console.error('Error loading activity:', error);
            const timeline = document.getElementById('activity-timeline');
            if (timeline) {
                timeline.innerHTML = '<div class="loading">Error loading activity</div>';
            }
        }
    }

    /**
     * Render a single activity item
     */
    renderActivityItem(item) {
        const type = item.type;
        const icon = type === 'job' ? 'J' : 'D';
        const iconClass = type === 'job' ? 'type-job' : 'type-deployment';
        const timestamp = this.formatDateTime(item.timestamp);

        let title = '';
        let description = '';

        if (type === 'job') {
            title = `Job #${item.id} - ${item.state}`;
            description = `${item.manufacturer || 'Unknown'} ${item.model || 'Unknown'}`;
            if (item.device_id) {
                description += ` (${item.device_id})`;
            }
        } else if (type === 'deployment') {
            title = `Deployment #${item.id} - ${item.status}`;
            description = `Package: ${item.package_name || 'Unknown'} | Ring: ${item.ring_name || 'Unknown'}`;
            if (item.device_count) {
                description += ` | Devices: ${item.device_count}`;
            }
        }

        return `
            <div class="activity-item">
                <div class="activity-icon ${iconClass}">${icon}</div>
                <div class="activity-content">
                    <div class="activity-title">${this.escapeHtml(title)}</div>
                    <div class="activity-description">${this.escapeHtml(description)}</div>
                </div>
                <div class="activity-timestamp">${timestamp}</div>
            </div>
        `;
    }

    /**
     * Fetch data from API endpoint
     */
    async fetchAPI(endpoint) {
        const response = await fetch(`${this.apiBase}${endpoint}`);

        if (!response.ok) {
            throw new Error(`API request failed: ${response.status} ${response.statusText}`);
        }

        return await response.json();
    }

    /**
     * Update element text content by ID
     */
    updateElement(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = value;
        }
    }

    /**
     * Update last refresh timestamp
     */
    updateLastRefreshTime() {
        const now = new Date();
        const timeString = now.toLocaleTimeString();
        this.updateElement('last-updated-time', timeString);
    }

    /**
     * Format datetime string
     */
    formatDateTime(dateString) {
        if (!dateString) return 'N/A';

        try {
            const date = new Date(dateString);
            const now = new Date();
            const diffMs = now - date;
            const diffMins = Math.floor(diffMs / 60000);
            const diffHours = Math.floor(diffMs / 3600000);
            const diffDays = Math.floor(diffMs / 86400000);

            // Show relative time for recent events
            if (diffMins < 1) {
                return 'Just now';
            } else if (diffMins < 60) {
                return `${diffMins} min${diffMins !== 1 ? 's' : ''} ago`;
            } else if (diffHours < 24) {
                return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`;
            } else if (diffDays < 7) {
                return `${diffDays} day${diffDays !== 1 ? 's' : ''} ago`;
            } else {
                // Show absolute date for older events
                return date.toLocaleString();
            }
        } catch (error) {
            return dateString;
        }
    }

    /**
     * Escape HTML to prevent XSS
     */
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Show error message
     */
    showError(message) {
        console.error(message);
        // Could implement a toast notification or banner here
    }

    /**
     * Start auto-refresh timer
     */
    startAutoRefresh() {
        if (this.refreshTimer) {
            clearInterval(this.refreshTimer);
        }

        this.refreshTimer = setInterval(() => {
            if (this.isRefreshEnabled) {
                this.loadAllData();
            }
        }, this.refreshInterval);

        this.updateElement('refresh-status', 'enabled');
    }

    /**
     * Stop auto-refresh timer
     */
    stopAutoRefresh() {
        if (this.refreshTimer) {
            clearInterval(this.refreshTimer);
            this.refreshTimer = null;
        }

        this.isRefreshEnabled = false;
        this.updateElement('refresh-status', 'disabled');
    }

    /**
     * Toggle auto-refresh
     */
    toggleAutoRefresh() {
        if (this.isRefreshEnabled) {
            this.stopAutoRefresh();
        } else {
            this.isRefreshEnabled = true;
            this.startAutoRefresh();
        }
    }
}

// Initialize dashboard when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.dashboard = new DashboardApp();
    });
} else {
    window.dashboard = new DashboardApp();
}
