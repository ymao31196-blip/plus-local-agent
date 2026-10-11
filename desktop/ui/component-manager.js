const componentErrorMessage = error => error instanceof Error ? error.message : String(error);
// Component Management UI
// Provider discovery, installation, upgrade, and lifecycle management

class ComponentManager {
    constructor(app) {
        this.app = app;
        this.repositories = [];
        this.availableProviders = [];
        this.installedProviders = [];
        this.selectedProvider = null;
    }

    async init() {
        await this.loadRepositories();
        await this.loadInstalledProviders();
    }

    async loadRepositories() {
        try {
            const result = await this.app.invoke('repository_list', {});
            this.repositories = result.repositories || [];
            return this.repositories;
        } catch (error) {
            console.error('Failed to load repositories:', error);
            this.repositories = [];
            return [];
        }
    }

    async addRepository(url, name = '', enabled = true) {
        try {
            const result = await this.app.invoke('repository_add', {
                url: url,
                name: name,
                enabled: enabled
            });
            await this.loadRepositories();
            return result;
        } catch (error) {
            throw new Error(`Failed to add repository: ${componentErrorMessage(error)}`);
        }
    }

    async removeRepository(url) {
        try {
            const result = await this.app.invoke('repository_remove', { url: url });
            await this.loadRepositories();
            return result;
        } catch (error) {
            throw new Error(`Failed to remove repository: ${componentErrorMessage(error)}`);
        }
    }

    async discoverProviders(repositoryUrl = null) {
        try {
            const args = repositoryUrl ? { repository_url: repositoryUrl } : {};
            const result = await this.app.invoke('provider_discover', args);
            this.availableProviders = result.providers || [];
            return result;
        } catch (error) {
            console.error('Failed to discover providers:', error);
            return { providers: [], total: 0, installed_count: 0 };
        }
    }

    async getProviderDetails(providerId, repositoryUrl) {
        try {
            return await this.app.invoke('provider_details_remote', {
                provider_id: providerId,
                repository_url: repositoryUrl
            });
        } catch (error) {
            throw new Error(`Failed to get provider details: ${componentErrorMessage(error)}`);
        }
    }

    async installProvider(providerId, repositoryUrl, version = null) {
        try {
            // Preview first
            const preview = await this.app.invoke('provider_install_remote', {
                provider_id: providerId,
                repository_url: repositoryUrl,
                version: version,
                confirmed: false,
                expected_sha256: null
            });

            if (!confirm('安装以下已校验组件？安装后需单独启用。\n' + JSON.stringify(preview, null, 2))) return null;
            // Confirm installation
            const confirmed = await this.app.invoke('provider_install_remote', {
                provider_id: providerId,
                repository_url: repositoryUrl,
                version: version,
                confirmed: true,
                expected_sha256: preview.sha256
            });

            await this.waitForJob(confirmed);
            await this.loadInstalledProviders();
            return confirmed;
        } catch (error) {
            throw new Error(`Installation failed: ${componentErrorMessage(error)}`);
        }
    }

    async upgradeProvider(providerId, repositoryUrl, toVersion = null) {
        try {
            // Preview first
            const preview = await this.app.invoke('provider_upgrade_remote', {
                provider_id: providerId,
                repository_url: repositoryUrl,
                to_version: toVersion,
                confirmed: false,
                expected_sha256: null
            });

            if (!confirm('升级以下已校验组件？\n' + JSON.stringify(preview, null, 2))) return null;
            // Confirm upgrade
            const confirmed = await this.app.invoke('provider_upgrade_remote', {
                provider_id: providerId,
                repository_url: repositoryUrl,
                to_version: toVersion,
                confirmed: true,
                expected_sha256: preview.sha256
            });

            await this.waitForJob(confirmed);
            await this.loadInstalledProviders();
            return confirmed;
        } catch (error) {
            throw new Error(`Upgrade failed: ${componentErrorMessage(error)}`);
        }
    }

    async uninstallProvider(providerId) {
        try {
            // Preview first
            const preview = await this.app.invoke('provider_uninstall_remote', {
                provider_id: providerId,
                confirmed: false,
                expected_sha256: null
            });

            if (!confirm('卸载以下组件的已登记文件与隔离环境？\n' + JSON.stringify(preview, null, 2))) return null;
            // Confirm uninstallation
            const confirmed = await this.app.invoke('provider_uninstall_remote', {
                provider_id: providerId,
                confirmed: true,
                expected_sha256: preview.sha256
            });

            await this.waitForJob(confirmed);
            await this.loadInstalledProviders();
            return confirmed;
        } catch (error) {
            throw new Error(`Uninstallation failed: ${componentErrorMessage(error)}`);
        }
    }

    async waitForJob(started) {
        if (!started.started || !started.job) throw new Error('后台任务未启动');
        while (true) {
            const status = await this.app.invoke('installation_status', {});
            const job = status.jobs.find(item => item.job === started.job);
            if (!job) throw new Error('安装任务丢失，请查看诊断日志');
            if (job.state === 'completed') return;
            if (job.state !== 'running') throw new Error((job.logs || []).join('\n') || '安装任务失败');
            this.app.showNotification('组件任务正在执行…\n' + (job.logs || []).slice(-3).join('\n'), 'info');
            await new Promise(resolve => setTimeout(resolve, 1000));
        }
    }

    async checkHealth(providerId) {
        try {
            return await this.app.invoke('provider_health_check', {
                provider_id: providerId
            });
        } catch (error) {
            console.error(`Health check failed for ${providerId}:`, error);
            return { status: 'error', error: componentErrorMessage(error) };
        }
    }

    async loadInstalledProviders() {
        try {
            const result = await this.app.invoke('provider_installed_list', {});
            this.installedProviders = result.providers || [];
            return this.installedProviders;
        } catch (error) {
            console.error('Failed to load installed providers:', error);
            this.installedProviders = [];
            return [];
        }
    }

    // UI Rendering

    renderRepositoryManager() {
        const container = document.createElement('div');
        container.className = 'repository-manager';

        const header = document.createElement('div');
        header.className = 'section-header';
        header.innerHTML = `
            <h3>Provider Repositories</h3>
            <button class="btn-primary" id="add-repository-btn">Add Repository</button>
        `;
        container.appendChild(header);

        const list = document.createElement('div');
        list.className = 'repository-list';

        if (this.repositories.length === 0) {
            list.innerHTML = '<p class="empty-state">No repositories configured. Add a repository to discover providers.</p>';
        } else {
            this.repositories.forEach(repo => {
                const item = this.renderRepositoryItem(repo);
                list.appendChild(item);
            });
        }

        container.appendChild(list);

        // Add repository dialog handler
        container.querySelector('#add-repository-btn').addEventListener('click', () => {
            this.showAddRepositoryDialog();
        });

        return container;
    }

    renderRepositoryItem(repo) {
        const item = document.createElement('div');
        item.className = 'repository-item';

        const info = document.createElement('div');
        info.className = 'repository-info';
        info.innerHTML = `
            <h4>${this.escapeHtml(repo.name || repo.repository_id)}</h4>
            <p class="repository-url">${this.escapeHtml(repo.url)}</p>
            <span class="badge ${repo.enabled ? 'badge-success' : 'badge-secondary'}">
                ${repo.enabled ? 'Enabled' : 'Disabled'}
            </span>
        `;

        const actions = document.createElement('div');
        actions.className = 'repository-actions';
        actions.innerHTML = `
            <button class="btn-secondary btn-sm" data-action="browse">Browse</button>
            <button class="btn-danger btn-sm" data-action="remove">Remove</button>
        `;

        item.appendChild(info);
        item.appendChild(actions);

        // Event handlers
        actions.querySelector('[data-action="browse"]').addEventListener('click', () => {
            this.browseRepository(repo.url);
        });

        actions.querySelector('[data-action="remove"]').addEventListener('click', async () => {
            if (confirm(`Remove repository "${repo.name}"?`)) {
                try {
                    await this.removeRepository(repo.url);
                    this.app.showNotification('Repository removed successfully', 'success');
                    this.app.renderCurrentView();
                } catch (error) {
                    this.app.showNotification(componentErrorMessage(error), 'error');
                }
            }
        });

        return item;
    }

    renderProviderMarketplace() {
        const container = document.createElement('div');
        container.className = 'provider-marketplace';

        const header = document.createElement('div');
        header.className = 'marketplace-header';
        header.innerHTML = `
            <h3>Available Providers</h3>
            <div class="marketplace-controls">
                <input type="text" id="provider-search" placeholder="Search providers..." class="search-input">
                <select id="category-filter" class="filter-select">
                    <option value="">All Categories</option>
                    <option value="automation">Automation</option>
                    <option value="documents">Documents</option>
                    <option value="system">System</option>
                    <option value="general">General</option>
                </select>
                <button class="btn-primary" id="refresh-providers-btn">Refresh</button>
            </div>
        `;
        container.appendChild(header);

        const content = document.createElement('div');
        content.className = 'marketplace-content';
        content.id = 'marketplace-content';

        if (this.availableProviders.length === 0) {
            content.innerHTML = '<p class="empty-state">No providers discovered. Add a repository and click Refresh.</p>';
        } else {
            const grid = document.createElement('div');
            grid.className = 'provider-grid';

            this.availableProviders.forEach(provider => {
                const card = this.renderProviderCard(provider);
                grid.appendChild(card);
            });

            content.appendChild(grid);
        }

        container.appendChild(content);

        // Event handlers
        header.querySelector('#refresh-providers-btn').addEventListener('click', async () => {
            await this.refreshProviderList();
        });

        header.querySelector('#provider-search').addEventListener('input', (e) => {
            this.filterProviders(e.target.value, '');
        });

        header.querySelector('#category-filter').addEventListener('change', (e) => {
            this.filterProviders('', e.target.value);
        });

        return container;
    }

    renderProviderCard(provider) {
        const card = document.createElement('div');
        card.className = 'provider-card';
        if (provider.installed) {
            card.classList.add('installed');
        }

        card.innerHTML = `
            <div class="provider-card-header">
                <h4>${this.escapeHtml(provider.name)}</h4>
                ${provider.installed ? '<span class="badge badge-success">Installed</span>' : ''}
                ${provider.upgrade_available ? '<span class="badge badge-warning">Update Available</span>' : ''}
            </div>
            <div class="provider-card-body">
                <p class="provider-description">${this.escapeHtml(provider.description)}</p>
                <div class="provider-meta">
                    <span class="badge badge-info">${this.escapeHtml(provider.category)}</span>
                    <span class="version">v${this.escapeHtml(provider.latest_version)}</span>
                </div>
            </div>
            <div class="provider-card-actions">
                ${this.renderProviderActions(provider)}
            </div>
        `;

        // Add event handlers
        const actions = card.querySelector('.provider-card-actions');

        if (!provider.installed) {
            const installBtn = actions.querySelector('[data-action="install"]');
            if (installBtn) {
                installBtn.addEventListener('click', async () => {
                    await this.handleInstallProvider(provider);
                });
            }
        } else {
            if (provider.upgrade_available) {
                const upgradeBtn = actions.querySelector('[data-action="upgrade"]');
                if (upgradeBtn) {
                    upgradeBtn.addEventListener('click', async () => {
                        await this.handleUpgradeProvider(provider);
                    });
                }
            }

            const uninstallBtn = actions.querySelector('[data-action="uninstall"]');
            if (uninstallBtn) {
                uninstallBtn.addEventListener('click', async () => {
                    await this.handleUninstallProvider(provider);
                });
            }

            const healthBtn = actions.querySelector('[data-action="health"]');
            if (healthBtn) {
                healthBtn.addEventListener('click', async () => {
                    await this.handleHealthCheck(provider);
                });
            }
        }

        const detailsBtn = actions.querySelector('[data-action="details"]');
        if (detailsBtn) {
            detailsBtn.addEventListener('click', async () => {
                await this.showProviderDetails(provider);
            });
        }

        return card;
    }

    renderProviderActions(provider) {
        if (!provider.installed) {
            return `
                <button class="btn-primary btn-sm" data-action="install">Install</button>
                <button class="btn-secondary btn-sm" data-action="details">Details</button>
            `;
        }

        let actions = '';
        if (provider.upgrade_available) {
            actions += '<button class="btn-warning btn-sm" data-action="upgrade">Upgrade</button>';
        }
        actions += `
            <button class="btn-secondary btn-sm" data-action="health">Health</button>
            <button class="btn-danger btn-sm" data-action="uninstall">Uninstall</button>
            <button class="btn-secondary btn-sm" data-action="details">Details</button>
        `;

        return actions;
    }

    // Action handlers

    async handleInstallProvider(provider) {
        try {
            this.app.showNotification(`Installing ${provider.name}...`, 'info');

            const result = await this.installProvider(
                provider.provider_id,
                provider.repository_url,
                null // Use latest version
            );

            if (!result) return;
            this.app.showNotification(`${provider.name} installed successfully!`, 'success');
            await this.refreshProviderList();
        } catch (error) {
            this.app.showNotification(`Installation failed: ${componentErrorMessage(error)}`, 'error');
        }
    }

    async handleUpgradeProvider(provider) {
        try {
            this.app.showNotification(`Upgrading ${provider.name}...`, 'info');

            const result = await this.upgradeProvider(
                provider.provider_id,
                provider.repository_url,
                null // Use latest version
            );

            if (!result) return;
            this.app.showNotification(`${provider.name} upgraded successfully!`, 'success');
            await this.refreshProviderList();
        } catch (error) {
            this.app.showNotification(`Upgrade failed: ${componentErrorMessage(error)}`, 'error');
        }
    }

    async handleUninstallProvider(provider) {
        try {
            this.app.showNotification(`Uninstalling ${provider.name}...`, 'info');

            const result = await this.uninstallProvider(provider.provider_id);
            if (!result) return;

            this.app.showNotification(`${provider.name} uninstalled successfully!`, 'success');
            await this.refreshProviderList();
        } catch (error) {
            this.app.showNotification(`Uninstallation failed: ${componentErrorMessage(error)}`, 'error');
        }
    }

    async handleHealthCheck(provider) {
        try {
            this.app.showNotification(`Checking ${provider.name} health...`, 'info');

            const result = await this.checkHealth(provider.provider_id);

            let message = `${provider.name} health: ${result.status}\n`;
            if (result.checks) {
                message += '\nChecks:\n';
                for (const [check, passed] of Object.entries(result.checks)) {
                    message += `  ${check}: ${passed ? '✓' : '✗'}\n`;
                }
            }

            alert(message);
        } catch (error) {
            this.app.showNotification(`Health check failed: ${componentErrorMessage(error)}`, 'error');
        }
    }

    async showProviderDetails(provider) {
        try {
            const details = await this.getProviderDetails(
                provider.provider_id,
                provider.repository_url
            );

            this.renderProviderDetailsDialog(details, provider);
        } catch (error) {
            this.app.showNotification(`Failed to load details: ${componentErrorMessage(error)}`, 'error');
        }
    }

    renderProviderDetailsDialog(details, provider) {
        const dialog = document.createElement('div');
        dialog.className = 'modal-overlay';

        dialog.innerHTML = `
            <div class="modal-content modal-large">
                <div class="modal-header">
                    <h2>${this.escapeHtml(details.name)}</h2>
                    <button class="modal-close">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="provider-details">
                        <section class="detail-section">
                            <h3>Information</h3>
                            <table class="detail-table">
                                <tr><td>ID:</td><td>${this.escapeHtml(details.provider_id)}</td></tr>
                                <tr><td>Author:</td><td>${this.escapeHtml(details.author)}</td></tr>
                                <tr><td>License:</td><td>${this.escapeHtml(details.license)}</td></tr>
                                <tr><td>Category:</td><td>${this.escapeHtml(details.category)}</td></tr>
                                <tr><td>Min PLA Version:</td><td>${this.escapeHtml(details.minimum_pla_version)}</td></tr>
                            </table>
                        </section>

                        <section class="detail-section">
                            <h3>Description</h3>
                            <p>${this.escapeHtml(details.description)}</p>
                        </section>

                        ${details.homepage ? `
                            <section class="detail-section">
                                <h3>Homepage</h3>
                                <a href="${this.escapeHtml(details.homepage)}" target="_blank">${this.escapeHtml(details.homepage)}</a>
                            </section>
                        ` : ''}

                        <section class="detail-section">
                            <h3>Available Versions</h3>
                            <div class="version-list">
                                ${details.versions.map(v => `
                                    <div class="version-item ${v.deprecated ? 'deprecated' : ''}">
                                        <span class="version-number">v${this.escapeHtml(v.version)}</span>
                                        <span class="badge badge-${this.getStabilityClass(v.stability)}">${this.escapeHtml(v.stability)}</span>
                                        ${v.breaking_changes ? '<span class="badge badge-danger">Breaking</span>' : ''}
                                        ${v.deprecated ? '<span class="badge badge-secondary">Deprecated</span>' : ''}
                                        <p class="version-changelog">${this.escapeHtml(v.changelog)}</p>
                                    </div>
                                `).join('')}
                            </div>
                        </section>

                        ${details.installed ? `
                            <section class="detail-section">
                                <h3>Installation Status</h3>
                                <p>Installed version: <strong>v${this.escapeHtml(details.installed_version)}</strong></p>
                                <p>State: <span class="badge">${this.escapeHtml(details.installed_state)}</span></p>
                            </section>
                        ` : ''}
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn-secondary" id="close-details">Close</button>
                </div>
            </div>
        `;

        document.body.appendChild(dialog);

        // Event handlers
        dialog.querySelector('.modal-close').addEventListener('click', () => {
            dialog.remove();
        });

        dialog.querySelector('#close-details').addEventListener('click', () => {
            dialog.remove();
        });

        dialog.addEventListener('click', (e) => {
            if (e.target === dialog) {
                dialog.remove();
            }
        });
    }

    showAddRepositoryDialog() {
        const dialog = document.createElement('div');
        dialog.className = 'modal-overlay';

        dialog.innerHTML = `
            <div class="modal-content">
                <div class="modal-header">
                    <h2>Add Provider Repository</h2>
                    <button class="modal-close">&times;</button>
                </div>
                <div class="modal-body">
                    <form id="add-repository-form">
                        <div class="form-group">
                            <label for="repo-url">Repository URL *</label>
                            <input type="text" id="repo-url" required
                                   placeholder="https://example.com/pla-providers or file:///path/to/repo"
                                   class="form-input">
                            <small>Supports https:// and file:// URLs</small>
                        </div>
                        <div class="form-group">
                            <label for="repo-name">Name (optional)</label>
                            <input type="text" id="repo-name"
                                   placeholder="Auto-detected from repository"
                                   class="form-input">
                        </div>
                        <div class="form-group">
                            <label>
                                <input type="checkbox" id="repo-enabled" checked>
                                Enable immediately
                            </label>
                        </div>
                    </form>
                </div>
                <div class="modal-footer">
                    <button class="btn-secondary" id="cancel-add-repo">Cancel</button>
                    <button class="btn-primary" id="confirm-add-repo">Add Repository</button>
                </div>
            </div>
        `;

        document.body.appendChild(dialog);

        // Event handlers
        const closeDialog = () => dialog.remove();

        dialog.querySelector('.modal-close').addEventListener('click', closeDialog);
        dialog.querySelector('#cancel-add-repo').addEventListener('click', closeDialog);

        dialog.querySelector('#confirm-add-repo').addEventListener('click', async () => {
            const url = dialog.querySelector('#repo-url').value.trim();
            const name = dialog.querySelector('#repo-name').value.trim();
            const enabled = dialog.querySelector('#repo-enabled').checked;

            if (!url) {
                alert('Please enter a repository URL');
                return;
            }

            try {
                await this.addRepository(url, name, enabled);
                this.app.showNotification('Repository added successfully!', 'success');
                closeDialog();
                this.app.renderCurrentView();
            } catch (error) {
                this.app.showNotification(componentErrorMessage(error), 'error');
            }
        });

        dialog.addEventListener('click', (e) => {
            if (e.target === dialog) closeDialog();
        });
    }

    async browseRepository(url) {
        try {
            this.app.showNotification('Loading providers...', 'info');
            await this.discoverProviders(url);
            this.app.currentView = 'marketplace';
            this.app.renderCurrentView();
        } catch (error) {
            this.app.showNotification(`Failed to browse repository: ${componentErrorMessage(error)}`, 'error');
        }
    }

    async refreshProviderList() {
        try {
            this.app.showNotification('Refreshing provider list...', 'info');
            const result = await this.discoverProviders();

            const content = document.getElementById('marketplace-content');
            if (content) {
                content.innerHTML = '';

                if (result.providers.length === 0) {
                    content.innerHTML = '<p class="empty-state">No providers found in configured repositories.</p>';
                } else {
                    const grid = document.createElement('div');
                    grid.className = 'provider-grid';

                    result.providers.forEach(provider => {
                        const card = this.renderProviderCard(provider);
                        grid.appendChild(card);
                    });

                    content.appendChild(grid);
                }
            }

            this.app.showNotification(
                `Found ${result.total} providers (${result.installed_count} installed)`,
                'success'
            );
        } catch (error) {
            this.app.showNotification(`Failed to refresh: ${componentErrorMessage(error)}`, 'error');
        }
    }

    filterProviders(searchText, category) {
        const filtered = this.availableProviders.filter(provider => {
            const matchesSearch = !searchText ||
                provider.name.toLowerCase().includes(searchText.toLowerCase()) ||
                provider.description.toLowerCase().includes(searchText.toLowerCase());

            const matchesCategory = !category || provider.category === category;

            return matchesSearch && matchesCategory;
        });

        const content = document.getElementById('marketplace-content');
        if (content) {
            content.innerHTML = '';

            const grid = document.createElement('div');
            grid.className = 'provider-grid';

            filtered.forEach(provider => {
                const card = this.renderProviderCard(provider);
                grid.appendChild(card);
            });

            content.appendChild(grid);
        }
    }

    // Utility methods

    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    getStabilityClass(stability) {
        switch (stability) {
            case 'stable': return 'success';
            case 'beta': return 'warning';
            case 'alpha': return 'danger';
            default: return 'secondary';
        }
    }
}

// Export for use in main app
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ComponentManager;
}
