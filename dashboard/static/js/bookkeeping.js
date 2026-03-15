(function () {
    class BookkeepingWorkspace {
        constructor() {
            this.state = {
                referenceData: null,
                portfolios: [],
                periods: [],
                listingsCatalog: [],
                listingTags: [],
                listingMappings: [],
                workspace: null,
                selectedPortfolioId: null,
                selectedPeriodId: null,
                currentStep: 1,
                activeSheetKey: null,
                selectedRow: null,
                editorMode: 'update',
                selectedUploadIds: new Set(),
                pendingUploadStages: new Set(),
                activeContextPanel: 'editor',
                isStepModalOpen: false,
                isEditingMappings: false,
                isEditingRevenueChannels: false,
                driveStatus: null,
                isExporting: false,
            };
            this.processingPolls = {};

            this.stepDefinitions = [
                { id: 1, title: 'Portfolio and listings', recommendation: 'Create or select a portfolio', copy: 'Set the Cotton Candy portfolio and workbook listing names.' },
                { id: 2, title: 'Month workspace', recommendation: 'Open a month workspace', copy: 'Scope uploads and approval to one month.' },
                { id: 3, title: 'Revenue ingestion', recommendation: 'Upload revenue statements', copy: 'Normalize revenue rows into the spreadsheet.' },
                { id: 4, title: 'Expense ingestion', recommendation: 'Upload expense evidence', copy: 'Itemize screenshots and PDFs into expense rows.' },
                { id: 5, title: 'Corroboration', recommendation: 'Upload corroboration statements', copy: 'Cross-check with bank, card, or Stripe statements.' },
                { id: 6, title: 'Review and approval', recommendation: 'Resolve review items', copy: 'Handle flagged rows and AI change proposals.' },
                { id: 7, title: 'Export', recommendation: 'Download the workbook', copy: 'Export the approved live workbook state.' },
            ];

            this.elements = {
                refreshWorkspaceBtn: document.getElementById('refreshWorkspaceBtn'),
                approveWorkspaceBtn: document.getElementById('approveWorkspaceBtn'),
                approveWorkspaceInlineBtn: document.getElementById('approveWorkspaceInlineBtn'),
                exportWorkbookBtn: document.getElementById('exportWorkbookBtn'),
                generateReportBtn: document.getElementById('generateReportBtn'),
                openCurrentStepBtn: document.getElementById('openCurrentStepBtn'),
                addSheetRowBtn: document.getElementById('addSheetRowBtn'),
                reprocessExpenseEvidenceBtn: document.getElementById('reprocessExpenseEvidenceBtn'),
                stepBackBtn: document.getElementById('stepBackBtn'),
                stepNextBtn: document.getElementById('stepNextBtn'),
                stepperNav: document.getElementById('stepperNav'),
                stepperProgressLabel: document.getElementById('stepperProgressLabel'),
                stepperCurrentTitle: document.getElementById('stepperCurrentTitle'),
                stepperProgressMeta: document.getElementById('stepperProgressMeta'),
                stepperRecommendedAction: document.getElementById('stepperRecommendedAction'),
                stepperProgressFill: document.getElementById('stepperProgressFill'),
                stepModal: document.getElementById('stepModal'),
                stepModalCloseBtn: document.getElementById('stepModalCloseBtn'),
                stepModalTitle: document.getElementById('stepModalTitle'),
                portfolioQuickSwitch: document.getElementById('portfolioQuickSwitch'),
                periodQuickSwitch: document.getElementById('periodQuickSwitch'),
                driveStatusTitle: document.getElementById('driveStatusTitle'),
                driveStatusMeta: document.getElementById('driveStatusMeta'),
                driveIdentityBadge: document.getElementById('driveIdentityBadge'),
                connectDriveBtn: document.getElementById('connectDriveBtn'),
                disconnectDriveBtn: document.getElementById('disconnectDriveBtn'),
                portfolioForm: document.getElementById('portfolioForm'),
                portfolioTagInput: document.getElementById('portfolioTagInput'),
                newPortfolioBtn: document.getElementById('newPortfolioBtn'),
                deletePortfolioBtn: document.getElementById('deletePortfolioBtn'),
                portfolioList: document.getElementById('portfolioList'),
                portfolioCountBadge: document.getElementById('portfolioCountBadge'),
                periodForm: document.getElementById('periodForm'),
                periodList: document.getElementById('periodList'),
                periodCountBadge: document.getElementById('periodCountBadge'),
                listingMappingTableBody: document.getElementById('listingMappingTableBody'),
                listingMappingHeaderRow: document.getElementById('listingMappingHeaderRow'),
                listingMappingSummary: document.getElementById('listingMappingSummary'),
                editListingMappingsBtn: document.getElementById('editListingMappingsBtn'),
                cancelListingMappingsBtn: document.getElementById('cancelListingMappingsBtn'),
                saveListingMappingsBtn: document.getElementById('saveListingMappingsBtn'),
                revenueUploadForm: document.getElementById('revenueUploadForm'),
                expenseUploadForm: document.getElementById('expenseUploadForm'),
                corroborationUploadForm: document.getElementById('corroborationUploadForm'),
                expenseFilesInput: document.getElementById('expenseFilesInput'),
                expenseNotesInput: document.getElementById('expenseNotesInput'),
                clearRevenueUploadsBtn: document.getElementById('clearRevenueUploadsBtn'),
                clearExpenseUploadsBtn: document.getElementById('clearExpenseUploadsBtn'),
                expenseUploadSubmitBtn: document.getElementById('expenseUploadSubmitBtn'),
                expenseStepUploadsList: document.getElementById('expenseStepUploadsList'),
                revenueUploadSummary: document.getElementById('revenueUploadSummary'),
                revenueChannelsConfig: document.getElementById('revenueChannelsConfig'),
                revenueCoverageSummary: document.getElementById('revenueCoverageSummary'),
                revenueCoverageList: document.getElementById('revenueCoverageList'),
                editRevenueChannelsBtn: document.getElementById('editRevenueChannelsBtn'),
                cancelRevenueChannelsBtn: document.getElementById('cancelRevenueChannelsBtn'),
                saveRevenueChannelsBtn: document.getElementById('saveRevenueChannelsBtn'),
                expenseUploadSummary: document.getElementById('expenseUploadSummary'),
                revenueUploadStatus: document.getElementById('revenueUploadStatus'),
                expenseUploadStatus: document.getElementById('expenseUploadStatus'),
                corroborationUploadStatus: document.getElementById('corroborationUploadStatus'),
                revenueSourceInput: document.getElementById('revenueSourceInput'),
                corroborationSourceInput: document.getElementById('corroborationSourceInput'),
                workspaceTitle: document.getElementById('workspaceTitle'),
                workspaceSubtitle: document.getElementById('workspaceSubtitle'),
                workspaceStatusPill: document.getElementById('workspaceStatusPill'),
                metricRevenueCoverage: document.getElementById('metricRevenueCoverage'),
                metricOwnerRevenue: document.getElementById('metricOwnerRevenue'),
                metricOwnerExpenses: document.getElementById('metricOwnerExpenses'),
                metricReviewQueue: document.getElementById('metricReviewQueue'),
                metricCorroboration: document.getElementById('metricCorroboration'),
                sheetTabs: document.getElementById('sheetTabs'),
                sheetGridContainer: document.getElementById('sheetGridContainer'),
                sheetMeta: document.getElementById('sheetMeta'),
                sheetMetaSummary: document.getElementById('sheetMetaSummary'),
                sheetStatusBadge: document.getElementById('sheetStatusBadge'),
                rowEditor: document.getElementById('rowEditor'),
                selectionBadge: document.getElementById('selectionBadge'),
                proposalList: document.getElementById('proposalList'),
                pendingProposalCount: document.getElementById('pendingProposalCount'),
                evidencePreview: document.getElementById('evidencePreview'),
                uploadsList: document.getElementById('uploadsList'),
                uploadCountBadge: document.getElementById('uploadCountBadge'),
                revisionList: document.getElementById('revisionList'),
                chatList: document.getElementById('chatList'),
                assistantForm: document.getElementById('assistantForm'),
                assistantMessageInput: document.getElementById('assistantMessageInput'),
                reportWorkspaceValue: document.getElementById('reportWorkspaceValue'),
                reportReadinessNote: document.getElementById('reportReadinessNote'),
                reportDriveValue: document.getElementById('reportDriveValue'),
                reportDriveMeta: document.getElementById('reportDriveMeta'),
                reportDriveIdentityBadge: document.getElementById('reportDriveIdentityBadge'),
                connectDriveInlineBtn: document.getElementById('connectDriveInlineBtn'),
                disconnectDriveInlineBtn: document.getElementById('disconnectDriveInlineBtn'),
                exportStatusPanel: document.getElementById('exportStatusPanel'),
                exportStatusTitle: document.getElementById('exportStatusTitle'),
                exportStatusCopy: document.getElementById('exportStatusCopy'),
            };
        }

        async init() {
            this.bindEvents();
            this.updateStepUi();
            await Promise.all([
                this.loadReferenceData(),
                this.loadDriveStatus(),
                this.loadListingTags(),
                this.loadListingsCatalog(),
                this.loadPortfolios(),
            ]);
            this.handleDriveAuthRedirectState();
        }

        bindEvents() {
            this.elements.portfolioForm.addEventListener('submit', (event) => this.handlePortfolioSubmit(event));
            this.elements.newPortfolioBtn.addEventListener('click', () => this.resetPortfolioForm());
            this.elements.deletePortfolioBtn.addEventListener('click', () => this.deleteSelectedPortfolio());
            if (this.elements.connectDriveBtn) {
                this.elements.connectDriveBtn.addEventListener('click', () => this.connectGoogleDrive());
            }
            if (this.elements.disconnectDriveBtn) {
                this.elements.disconnectDriveBtn.addEventListener('click', () => this.disconnectGoogleDrive());
            }
            if (this.elements.connectDriveInlineBtn) {
                this.elements.connectDriveInlineBtn.addEventListener('click', () => this.connectGoogleDrive());
            }
            if (this.elements.disconnectDriveInlineBtn) {
                this.elements.disconnectDriveInlineBtn.addEventListener('click', () => this.disconnectGoogleDrive());
            }
            this.elements.editListingMappingsBtn.addEventListener('click', () => this.setListingMappingsEditMode(true));
            this.elements.cancelListingMappingsBtn.addEventListener('click', () => this.cancelListingMappingsEdit());
            this.elements.periodForm.addEventListener('submit', (event) => this.handlePeriodSubmit(event));
            this.elements.revenueUploadForm.addEventListener('submit', (event) => this.handleRevenueUpload(event));
            this.elements.expenseUploadForm.addEventListener('submit', (event) => this.handleExpenseUpload(event));
            this.elements.corroborationUploadForm.addEventListener('submit', (event) => this.handleCorroborationUpload(event));
            this.elements.clearRevenueUploadsBtn.addEventListener('click', () => this.clearUploadsByStage('revenue'));
            this.elements.clearExpenseUploadsBtn.addEventListener('click', () => this.clearUploadsByStage('expense'));
            this.elements.editRevenueChannelsBtn.addEventListener('click', () => this.setRevenueChannelsEditMode(true));
            this.elements.cancelRevenueChannelsBtn.addEventListener('click', () => this.cancelRevenueChannelsEdit());
            this.elements.saveRevenueChannelsBtn.addEventListener('click', () => this.saveRevenueChannels());
            if (this.elements.refreshWorkspaceBtn) {
                this.elements.refreshWorkspaceBtn.addEventListener('click', () => this.refreshWorkspace());
            }
            if (this.elements.approveWorkspaceBtn) {
                this.elements.approveWorkspaceBtn.addEventListener('click', () => this.approveWorkspace());
            }
            this.elements.approveWorkspaceInlineBtn.addEventListener('click', () => this.approveWorkspace());
            if (this.elements.exportWorkbookBtn) {
                this.elements.exportWorkbookBtn.addEventListener('click', () => this.exportWorkbook());
            }
            this.elements.generateReportBtn.addEventListener('click', () => this.exportWorkbook());
            if (this.elements.openCurrentStepBtn) {
                this.elements.openCurrentStepBtn.addEventListener('click', () => this.openStepModal());
            }
            this.elements.reprocessExpenseEvidenceBtn.addEventListener('click', () => this.reprocessExpenseEvidence());
            this.elements.saveListingMappingsBtn.addEventListener('click', () => this.saveListingMappings());
            this.elements.addSheetRowBtn.addEventListener('click', () => this.startCreateRow());
            this.elements.assistantForm.addEventListener('submit', (event) => this.handleAssistantSubmit(event));
            this.elements.stepBackBtn.addEventListener('click', () => this.goToRelativeStep(-1));
            this.elements.stepNextBtn.addEventListener('click', () => this.goToRelativeStep(1));
            this.elements.stepModalCloseBtn.addEventListener('click', () => this.closeStepModal());

            this.elements.stepModal.addEventListener('click', (event) => {
                if (event.target === this.elements.stepModal) {
                    this.closeStepModal();
                }
            });

            this.elements.stepperNav.querySelectorAll('[data-step-target]').forEach((button) => {
                button.addEventListener('click', () => this.setCurrentStep(Number(button.dataset.stepTarget), { openModal: true }));
            });

            document.querySelectorAll('[data-context-tab]').forEach((button) => {
                button.addEventListener('click', () => this.setActiveContextPanel(button.dataset.contextTab));
            });

            this.elements.portfolioQuickSwitch.addEventListener('change', (event) => {
                const portfolioId = Number(event.target.value);
                if (!portfolioId) return;
                this.selectPortfolio(portfolioId);
            });

            this.elements.periodQuickSwitch.addEventListener('change', (event) => {
                const periodId = Number(event.target.value);
                if (!periodId) return;
                this.selectPeriod(periodId);
            });

            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape' && this.state.isStepModalOpen) {
                    this.closeStepModal();
                }
            });

            this.elements.portfolioList.addEventListener('click', (event) => {
                const card = event.target.closest('[data-portfolio-id]');
                if (!card) return;
                this.selectPortfolio(Number(card.dataset.portfolioId));
            });

            this.elements.periodList.addEventListener('click', (event) => {
                const card = event.target.closest('[data-period-id]');
                if (!card) return;
                this.selectPeriod(Number(card.dataset.periodId));
            });

            this.elements.sheetTabs.addEventListener('click', (event) => {
                const button = event.target.closest('[data-sheet-key]');
                if (!button) return;
                this.setActiveSheet(button.dataset.sheetKey);
            });

            this.elements.sheetGridContainer.addEventListener('click', (event) => {
                const row = event.target.closest('tr[data-row-type][data-row-id]');
                if (!row) return;
                const rowType = row.dataset.rowType;
                const rowId = Number(row.dataset.rowId);
                if (!rowType || !rowId) return;
                this.selectRow(rowType, rowId);
            });

            this.elements.rowEditor.addEventListener('submit', (event) => this.handleRowEditorSubmit(event));
            this.elements.rowEditor.addEventListener('change', (event) => this.handleEditorDependentChange(event));
            this.elements.proposalList.addEventListener('click', (event) => this.handleProposalListClick(event));
            this.elements.uploadsList.addEventListener('click', (event) => this.handleUploadListClick(event));
            this.elements.uploadsList.addEventListener('change', (event) => this.handleUploadListChange(event));
            if (this.elements.expenseStepUploadsList) {
                this.elements.expenseStepUploadsList.addEventListener('click', (event) => this.handleUploadListClick(event));
                this.elements.expenseStepUploadsList.addEventListener('change', (event) => this.handleUploadListChange(event));
            }
        }

        async fetchJson(url, options = {}) {
            const response = await fetch(url, options);
            const contentType = response.headers.get('content-type') || '';
            const data = contentType.includes('application/json') ? await response.json() : null;
            if (!response.ok) {
                throw new Error((data && data.error) || `Request failed (${response.status})`);
            }
            return data;
        }

        async loadReferenceData() {
            this.state.referenceData = await this.fetchJson('/bookkeeping/api/reference-data');
            this.populateReferenceData();
        }

        async loadDriveStatus() {
            try {
                this.state.driveStatus = await this.fetchJson('/auth/api/google-drive-status');
            } catch (error) {
                this.state.driveStatus = {
                    connected: false,
                    mode: 'not_connected',
                    effective_mode: 'not_connected',
                    service_account_fallback_available: false,
                };
            }
            this.renderDriveStatus();
        }

        async loadListingTags() {
            const data = await this.fetchJson('/bookkeeping/api/listing-tags');
            this.state.listingTags = data.tags || [];
            this.populateTagOptions();
        }

        populateReferenceData() {
            this.populateRevenueSourceOptions();
            this.renderRevenueChannelConfig();
        }

        renderDriveStatus() {
            const status = this.state.driveStatus || {};
            const mode = status.effective_mode || status.mode || 'not_connected';
            const displayName = status.display_name || status.google_email || this.currentUserName() || 'your Google account';
            let title = 'Not connected';
            let meta = 'Connect Google Drive so exports can upload evidence and embed receipt links.';

            if (mode === 'user_authorized') {
                title = `Connected as ${displayName}`;
                meta = 'Exports will upload evidence into the configured Drive folder using your Google account.';
            } else if (mode === 'service_account') {
                title = 'Using service account fallback';
                meta = 'Exports can still sync evidence, but they are not using your signed-in Google account yet.';
            } else if (status.service_account_fallback_available) {
                title = 'Using service account fallback';
                meta = 'Drive sync is available through the shared service account. Connect your own Drive if you want exports written with your Google account.';
            }

            if (this.elements.driveStatusTitle) {
                this.elements.driveStatusTitle.textContent = title;
            }
            if (this.elements.driveStatusMeta) {
                this.elements.driveStatusMeta.textContent = meta;
            }
            if (this.elements.reportDriveValue) {
                this.elements.reportDriveValue.textContent = title;
            }
            if (this.elements.reportDriveMeta) {
                this.elements.reportDriveMeta.textContent = meta;
            }

            const isUserAuthorized = mode === 'user_authorized';
            this.setElementVisibility(this.elements.connectDriveBtn, !isUserAuthorized, 'inline-flex');
            this.setElementVisibility(this.elements.disconnectDriveBtn, isUserAuthorized, 'inline-flex');
            this.setElementVisibility(this.elements.connectDriveInlineBtn, !isUserAuthorized, 'inline-flex');
            this.setElementVisibility(this.elements.disconnectDriveInlineBtn, isUserAuthorized, 'inline-flex');
            this.setElementVisibility(this.elements.driveIdentityBadge, isUserAuthorized, 'inline-flex');
            this.setElementVisibility(this.elements.reportDriveIdentityBadge, isUserAuthorized, 'inline-flex');
            if (this.elements.driveIdentityBadge) {
                this.elements.driveIdentityBadge.textContent = isUserAuthorized ? 'Google Drive connected' : '';
            }
            if (this.elements.reportDriveIdentityBadge) {
                this.elements.reportDriveIdentityBadge.textContent = isUserAuthorized ? 'Google Drive connected' : '';
            }

            this.syncExportControls();
        }

        connectGoogleDrive() {
            const nextPath = `${window.location.pathname}${window.location.search || ''}`;
            window.location.href = `/auth/google-drive/connect?next=${encodeURIComponent(nextPath)}`;
        }

        async disconnectGoogleDrive() {
            if (!window.confirm('Disconnect Google Drive for this Cotton Candy account? Future exports will stop using your Google account until you reconnect it.')) {
                return;
            }
            await this.fetchJson('/auth/api/google-drive-disconnect', { method: 'DELETE' });
            await this.loadDriveStatus();
        }

        handleDriveAuthRedirectState() {
            const params = new URLSearchParams(window.location.search);
            const driveAuth = params.get('driveAuth');
            if (!driveAuth) {
                return;
            }

            if (driveAuth === 'connected') {
                window.alert('Google Drive is connected. Future bookkeeping exports will upload evidence into the configured Drive folder using your Google account.');
            } else if (driveAuth === 'error') {
                const reason = params.get('driveAuthMessage') || 'unknown_error';
                const messages = {
                    state_mismatch: 'The Google Drive authorization could not be verified. Please try connecting again.',
                    missing_code: 'Google did not return an authorization code. Please try again.',
                    token_exchange_failed: 'Cotton Candy could not exchange the Google Drive authorization code for tokens. Check the Google OAuth redirect URI and try again.',
                    userinfo_failed: 'Cotton Candy could not verify the Google account that authorized Drive access. Please try again.',
                    email_mismatch: 'The Google account used for Drive authorization did not match the signed-in Cotton Candy account.',
                    callback_failed: 'Google Drive authorization failed before Cotton Candy could save the token. Please try again.',
                };
                window.alert(messages[reason] || 'Google Drive authorization failed. Please try again.');
            }

            params.delete('driveAuth');
            params.delete('driveAuthMessage');
            const nextQuery = params.toString();
            const nextUrl = nextQuery ? `${window.location.pathname}?${nextQuery}` : window.location.pathname;
            window.history.replaceState({}, document.title, nextUrl);
        }

        currentUserName() {
            return document.querySelector('.bk-page')?.dataset.currentUserName || '';
        }

        setElementVisibility(element, isVisible, displayValue = 'block') {
            if (!element) return;
            element.hidden = !isVisible;
            element.style.display = isVisible ? displayValue : 'none';
        }

        setButtonBusy(button, isBusy, idleLabel, busyLabel) {
            if (!button) return;
            if (!button.dataset.idleLabel) {
                button.dataset.idleLabel = idleLabel || button.textContent.trim();
            }
            const defaultLabel = idleLabel || button.dataset.idleLabel;
            button.textContent = isBusy ? (busyLabel || defaultLabel) : defaultLabel;
            button.disabled = isBusy;
        }

        syncExportControls() {
            const isExporting = Boolean(this.state.isExporting);
            this.setButtonBusy(this.elements.exportWorkbookBtn, isExporting, 'Download workbook', 'Preparing workbook...');
            this.setButtonBusy(this.elements.generateReportBtn, isExporting, 'Download live workbook', 'Preparing workbook...');
            if (this.elements.connectDriveBtn) {
                this.elements.connectDriveBtn.disabled = isExporting;
            }
            if (this.elements.disconnectDriveBtn) {
                this.elements.disconnectDriveBtn.disabled = isExporting;
            }
            if (this.elements.connectDriveInlineBtn) {
                this.elements.connectDriveInlineBtn.disabled = isExporting;
            }
            if (this.elements.disconnectDriveInlineBtn) {
                this.elements.disconnectDriveInlineBtn.disabled = isExporting;
            }
        }

        setExportState(isExporting, title = 'Preparing workbook', copy = 'Cotton Candy is syncing evidence, building the workbook, and your download will start automatically.') {
            this.state.isExporting = Boolean(isExporting);
            this.syncExportControls();
            if (this.elements.exportStatusTitle) {
                this.elements.exportStatusTitle.textContent = title;
            }
            if (this.elements.exportStatusCopy) {
                this.elements.exportStatusCopy.textContent = copy;
            }
            if (this.elements.exportStatusPanel) {
                this.elements.exportStatusPanel.hidden = !isExporting;
                this.elements.exportStatusPanel.classList.toggle('is-visible', isExporting);
            }
        }

        channelDisplayLabel(source) {
            const overrides = {
                booking_com: 'BDC / Booking.com',
                direct_bookings: 'Direct',
            };
            const fallback = (this.state.referenceData?.revenue_sources || []).find((entry) => entry.value === source)?.label;
            return overrides[source] || fallback || source;
        }

        getConfiguredRevenueSources() {
            const workspaceChannels = this.state.workspace?.revenue_channels || [];
            if (workspaceChannels.length) {
                return workspaceChannels;
            }
            const portfolio = this.getSelectedPortfolio();
            if (portfolio?.revenue_channels?.length) {
                return portfolio.revenue_channels;
            }
            return this.state.referenceData?.default_revenue_channels || [];
        }

        populateRevenueSourceOptions() {
            const referenceData = this.state.referenceData || {};
            if (!this.elements.revenueSourceInput) return;
            const configured = this.getConfiguredRevenueSources();
            const visibleSources = configured.length ? configured : (referenceData.revenue_sources || []).map((source) => source.value);
            this.elements.revenueSourceInput.innerHTML = '<option value="auto">Auto-detect from headers / sheet names</option>';
            for (const source of referenceData.revenue_sources || []) {
                if (!visibleSources.includes(source.value)) {
                    continue;
                }
                const option = document.createElement('option');
                option.value = source.value;
                option.textContent = this.channelDisplayLabel(source.value);
                this.elements.revenueSourceInput.appendChild(option);
            }
        }

        renderRevenueChannelConfig() {
            if (!this.elements.revenueChannelsConfig || !this.elements.revenueCoverageList || !this.elements.revenueCoverageSummary) {
                return;
            }
            const availableSources = this.state.referenceData?.revenue_sources || [];
            const configuredSources = this.getConfiguredRevenueSources();
            const configured = new Set(configuredSources);
            const checklist = this.state.workspace?.revenue_checklist || [];
            const hasPortfolio = Boolean(this.state.selectedPortfolioId);
            const isEditing = hasPortfolio && this.state.isEditingRevenueChannels;

            if (!hasPortfolio) {
                this.elements.revenueChannelsConfig.innerHTML = '<div class="bk-empty">Select a portfolio to configure expected revenue channels.</div>';
                this.elements.revenueCoverageSummary.textContent = 'Open a portfolio and month workspace to see uploaded and missing channels.';
                this.elements.revenueCoverageList.innerHTML = '<div class="bk-empty">Revenue channel coverage will appear here.</div>';
                this.elements.saveRevenueChannelsBtn.disabled = true;
                this.elements.editRevenueChannelsBtn.hidden = true;
                this.elements.cancelRevenueChannelsBtn.hidden = true;
                this.elements.saveRevenueChannelsBtn.hidden = true;
                this.populateRevenueSourceOptions();
                return;
            }

            this.elements.editRevenueChannelsBtn.hidden = isEditing;
            this.elements.cancelRevenueChannelsBtn.hidden = !isEditing;
            this.elements.saveRevenueChannelsBtn.hidden = !isEditing;
            this.elements.saveRevenueChannelsBtn.disabled = false;

            if (isEditing) {
                this.elements.revenueChannelsConfig.innerHTML = availableSources.map((source) => `
                    <label class="bk-channel-option">
                        <input type="checkbox" data-revenue-channel value="${source.value}" ${configured.has(source.value) ? 'checked' : ''}>
                        <span>
                            <strong>${this.escapeHtml(this.channelDisplayLabel(source.value))}</strong>
                            <span>${configured.has(source.value) ? 'Expected for this portfolio' : 'Not expected for this portfolio'}</span>
                        </span>
                    </label>
                `).join('');
            } else if (configuredSources.length) {
                this.elements.revenueChannelsConfig.innerHTML = `
                    <div class="bk-channel-saved">
                        ${configuredSources.map((source) => `
                            <div class="bk-channel-saved-item">
                                <strong>${this.escapeHtml(this.channelDisplayLabel(source))}</strong>
                                <span>Expected for this portfolio</span>
                            </div>
                        `).join('')}
                    </div>
                `;
            } else {
                this.elements.revenueChannelsConfig.innerHTML = '<div class="bk-empty">No revenue channels are configured yet. Click edit channels to choose them.</div>';
            }

            const configuredCount = configured.size;
            const uploadedCount = checklist.filter((entry) => entry.expected && entry.status === 'uploaded').length;
            const capturedCount = checklist.filter((entry) => entry.expected && entry.status === 'captured').length;
            const missingCount = checklist.filter((entry) => entry.expected && entry.missing).length;
            this.elements.revenueCoverageSummary.textContent = this.state.selectedPeriodId
                ? `${uploadedCount + capturedCount} of ${configuredCount} expected channels are present for this month. ${missingCount} still missing.`
                : `${configuredCount} expected channel${configuredCount === 1 ? '' : 's'} configured for this portfolio. Open a month workspace to track coverage.`;

            const coverageRows = checklist.length
                ? checklist
                : Array.from(configured).map((source) => ({
                    source,
                    label: this.channelDisplayLabel(source),
                    expected: true,
                    status: 'missing',
                    missing: true,
                    upload_count: 0,
                    row_count: 0,
                    gross_total: 0,
                }));

            this.elements.revenueCoverageList.innerHTML = coverageRows.map((entry) => {
                const label = entry.label || this.channelDisplayLabel(entry.source);
                const detailParts = [];
                if (entry.upload_count) {
                    detailParts.push(`${entry.upload_count} upload${entry.upload_count === 1 ? '' : 's'}`);
                }
                if (entry.row_count) {
                    detailParts.push(`${entry.row_count} row${entry.row_count === 1 ? '' : 's'}`);
                }
                if (entry.gross_total) {
                    detailParts.push(this.formatCurrency(entry.gross_total));
                }
                if (!detailParts.length) {
                    detailParts.push(entry.expected ? 'Waiting for upload' : 'Detected from uploaded revenue files');
                }
                const statusLabelMap = {
                    uploaded: 'Uploaded',
                    captured: 'Rows present',
                    missing: 'Missing',
                    unexpected: 'Unexpected',
                };
                return `
                    <div class="bk-channel-status-item">
                        <div class="bk-channel-status-copy">
                            <strong>${this.escapeHtml(label)}</strong>
                            <span>${this.escapeHtml(detailParts.join(' · '))}</span>
                        </div>
                        <span class="bk-channel-state ${this.escapeHtml(entry.status || 'missing')}">${this.escapeHtml(statusLabelMap[entry.status] || 'Missing')}</span>
                    </div>
                `;
            }).join('');

            this.populateRevenueSourceOptions();
        }

        setRevenueChannelsEditMode(isEditing) {
            this.state.isEditingRevenueChannels = Boolean(isEditing);
            this.renderRevenueChannelConfig();
        }

        cancelRevenueChannelsEdit() {
            this.state.isEditingRevenueChannels = false;
            this.renderRevenueChannelConfig();
        }

        populateTagOptions(selectedTag = null) {
            if (!this.elements.portfolioTagInput) return;
            const currentValue = selectedTag ?? this.elements.portfolioTagInput.value ?? '';
            const tags = [...(this.state.listingTags || [])];
            if (currentValue && !tags.some((tag) => tag.name === currentValue)) {
                tags.unshift({ name: currentValue, usage_count: 0 });
            }
            this.elements.portfolioTagInput.innerHTML = ['<option value="">Choose tag</option>']
                .concat(tags.map((tag) => `<option value="${this.escapeHtml(tag.name)}">${this.escapeHtml(tag.name)}${tag.usage_count ? ` (${tag.usage_count})` : ''}</option>`))
                .join('');
            this.elements.portfolioTagInput.value = currentValue || '';
        }

        async loadListingsCatalog() {
            const data = await this.fetchJson('/bookkeeping/api/listings/catalog');
            this.state.listingsCatalog = data.listings || [];
            this.renderListingMappings();
        }

        async loadPortfolios() {
            const data = await this.fetchJson('/bookkeeping/api/portfolios');
            this.state.portfolios = data.portfolios || [];
            this.renderPortfolioList();
        }

        async loadListingMappings(portfolioId) {
            const data = await this.fetchJson(`/bookkeeping/api/portfolios/${portfolioId}/listing-mappings`);
            this.state.listingsCatalog = data.listings || this.state.listingsCatalog;
            this.state.listingMappings = data.listing_mappings || [];
            if (data.portfolio) {
                this.upsertPortfolioState(data.portfolio);
            }
            this.renderListingMappings();
        }

        upsertPortfolioState(portfolio) {
            const nextPortfolios = [...(this.state.portfolios || [])];
            const index = nextPortfolios.findIndex((entry) => entry.bookkeeping_portfolio_id === portfolio.bookkeeping_portfolio_id);
            if (index >= 0) {
                nextPortfolios[index] = { ...nextPortfolios[index], ...portfolio };
            } else {
                nextPortfolios.unshift(portfolio);
            }
            this.state.portfolios = nextPortfolios;
            this.renderPortfolioList();
        }

        async loadPeriods(portfolioId) {
            const data = await this.fetchJson(`/bookkeeping/api/portfolios/${portfolioId}/periods`);
            this.state.periods = data.periods || [];
            this.renderPeriodList();
        }

        async refreshWorkspace() {
            if (!this.state.selectedPeriodId) {
                this.renderWorkspace(null);
                return;
            }
            const workspace = await this.fetchJson(`/bookkeeping/api/periods/${this.state.selectedPeriodId}/workspace`);
            this.state.workspace = workspace;
            this.renderWorkspace(workspace);
        }

        renderPortfolioList() {
            this.elements.portfolioCountBadge.textContent = String(this.state.portfolios.length);
            this.renderPortfolioQuickSwitch();
            if (this.elements.deletePortfolioBtn) {
                this.elements.deletePortfolioBtn.disabled = !this.state.selectedPortfolioId;
            }
            if (!this.state.portfolios.length) {
                this.elements.portfolioList.innerHTML = '<div class="bk-empty">No bookkeeping portfolios yet.</div>';
                return;
            }

            this.elements.portfolioList.innerHTML = this.state.portfolios.map((portfolio) => `
                <div class="bk-list-item ${portfolio.bookkeeping_portfolio_id === this.state.selectedPortfolioId ? 'is-active' : ''}" data-portfolio-id="${portfolio.bookkeeping_portfolio_id}">
                    <strong>${this.escapeHtml(portfolio.name || portfolio.code || `Portfolio ${portfolio.bookkeeping_portfolio_id}`)}</strong>
                    <span>${(portfolio.portfolio_tag || portfolio.listing_tag) ? `Portfolio tag ${this.escapeHtml(portfolio.portfolio_tag || portfolio.listing_tag)} · ` : ''}${portfolio.period_count || 0} month workspace${(portfolio.period_count || 0) === 1 ? '' : 's'} · ${(portfolio.listing_mapping_count || 0)} mapped listings</span>
                </div>
            `).join('');
        }

        renderPeriodList() {
            this.elements.periodCountBadge.textContent = String(this.state.periods.length);
            this.renderPeriodQuickSwitch();
            if (!this.state.selectedPortfolioId) {
                this.elements.periodList.innerHTML = '<div class="bk-empty">Select a portfolio first.</div>';
                return;
            }
            if (!this.state.periods.length) {
                this.elements.periodList.innerHTML = '<div class="bk-empty">No months yet for this portfolio.</div>';
                return;
            }
            this.elements.periodList.innerHTML = this.state.periods.map((period) => `
                <div class="bk-list-item ${period.bookkeeping_period_id === this.state.selectedPeriodId ? 'is-active' : ''}" data-period-id="${period.bookkeeping_period_id}">
                    <strong>${this.escapeHtml(period.name)}</strong>
                    <span>${this.escapeHtml(period.period_start || '')} to ${this.escapeHtml(period.period_end || '')} · ${(period.status || 'draft').toUpperCase()}</span>
                </div>
            `).join('');
        }

        renderListingMappings() {
            if (!this.state.selectedPortfolioId) {
                this.elements.listingMappingTableBody.innerHTML = '<tr><td colspan="5" class="bk-empty">Select a portfolio to configure its listing mappings.</td></tr>';
                this.elements.editListingMappingsBtn.hidden = true;
                this.elements.cancelListingMappingsBtn.hidden = true;
                this.elements.saveListingMappingsBtn.hidden = true;
                return;
            }

            const portfolio = this.getSelectedPortfolio();
            const mappings = [...(this.state.listingMappings || [])].sort((left, right) => String(left.official_name || '').localeCompare(String(right.official_name || '')));
            const mappingByListingId = new Map((this.state.listingMappings || []).map((mapping) => [mapping.listing_id, mapping]));
            if (!this.state.listingsCatalog.length) {
                this.elements.listingMappingTableBody.innerHTML = '<tr><td colspan="5" class="bk-empty">No Cotton Candy listings were found.</td></tr>';
                return;
            }

            const isEditing = this.state.isEditingMappings || !mappings.length;
            this.elements.editListingMappingsBtn.hidden = isEditing;
            this.elements.cancelListingMappingsBtn.hidden = !isEditing || !mappings.length;
            this.elements.saveListingMappingsBtn.hidden = !isEditing;
            const portfolioTag = portfolio?.portfolio_tag || portfolio?.listing_tag || '';

            if (!isEditing && mappings.length) {
                this.elements.listingMappingSummary.textContent = `${mappings.length} unit${mappings.length === 1 ? '' : 's'} are locked into this portfolio.${portfolioTag ? ` Current portfolio tag: '${portfolioTag}'.` : ''} Click edit only when you need to change the unit list or aliases.`;
                this.elements.listingMappingTableBody.innerHTML = mappings.map((mapping) => `
                    <tr>
                        <td><span class="bk-badge">Mapped</span></td>
                        <td>${this.escapeHtml(mapping.official_name || '—')}</td>
                        <td>${this.escapeHtml(mapping.listing_name || '—')}</td>
                        <td>${this.escapeHtml(mapping.internal_listing_name || '—')}</td>
                        <td>${this.escapeHtml((mapping.aliases || []).join(', ') || '—')}</td>
                    </tr>
                `).join('');
                return;
            }

            this.elements.listingMappingSummary.textContent = portfolioTag
                ? `Editing the mapping set using candidate units from portfolio tag '${portfolioTag}'. Save when you’re done changing which listings belong to this portfolio.`
                : 'Pick the listings that belong to this portfolio and define the workbook name and alias memory used by the parsers.';

            this.elements.listingMappingTableBody.innerHTML = this.state.listingsCatalog.map((listing) => {
                const mapping = mappingByListingId.get(listing.listing_id);
                const isChecked = Boolean(mapping);
                const officialName = mapping?.official_name || listing.internal_listing_name || listing.name || `Listing ${listing.listing_id}`;
                const aliases = (mapping?.aliases || []).join(', ');
                return `
                    <tr data-listing-row data-listing-id="${listing.listing_id}">
                        <td><input type="checkbox" data-mapping-active ${isChecked ? 'checked' : ''}></td>
                        <td><input type="text" data-mapping-official value="${this.escapeHtml(officialName)}"></td>
                        <td>${this.escapeHtml(listing.name || `Listing ${listing.listing_id}`)}</td>
                        <td>${this.escapeHtml(listing.internal_listing_name || '—')}</td>
                        <td><input type="text" data-mapping-aliases value="${this.escapeHtml(aliases)}" placeholder="Alias 1, Alias 2"></td>
                    </tr>
                `;
            }).join('');
        }

        getSelectedPortfolio() {
            return (this.state.portfolios || []).find((entry) => entry.bookkeeping_portfolio_id === this.state.selectedPortfolioId) || null;
        }

        setListingMappingsEditMode(isEditing) {
            this.state.isEditingMappings = Boolean(isEditing);
            this.renderListingMappings();
        }

        cancelListingMappingsEdit() {
            this.state.isEditingMappings = false;
            this.loadListingMappings(this.state.selectedPortfolioId);
        }

        renderWorkspace(workspace) {
            this.state.workspace = workspace || null;
            const existingUploadIds = new Set((workspace?.uploads || []).map((upload) => upload.bookkeeping_upload_id));
            this.state.selectedUploadIds = new Set(
                Array.from(this.state.selectedUploadIds).filter((uploadId) => existingUploadIds.has(uploadId))
            );
            this.syncProcessingBatchPolling(workspace);
            const summary = workspace?.summary_cards || {};
            const period = workspace?.period || {};
            const portfolio = workspace?.portfolio || {};
            const configuredRevenueChannels = this.getConfiguredRevenueSources();
            const revenueProgress = workspace?.revenue_progress || {
                completed: 0,
                total: configuredRevenueChannels.length || this.state.referenceData?.default_revenue_channels?.length || 6,
            };
            const corroborationState = workspace?.corroboration_state || { matched_count: 0, rows: [] };

            this.elements.workspaceTitle.textContent = workspace ? `${portfolio.name || portfolio.code || 'Portfolio'} · ${period.name || ''}`.trim() : 'No workspace selected';
            this.elements.workspaceSubtitle.textContent = workspace
                ? `${portfolio.name || portfolio.property_name || ''}${portfolio.property_address ? ' · ' + portfolio.property_address : ''}`
                : 'Choose a portfolio and month to start the live spreadsheet.';
            this.elements.workspaceStatusPill.textContent = (period.status || 'draft').replace('_', ' ').toUpperCase();

            this.elements.metricRevenueCoverage.textContent = `${revenueProgress.completed || 0} / ${revenueProgress.total || configuredRevenueChannels.length || 6}`;
            this.elements.metricOwnerRevenue.textContent = this.formatCurrency(summary.owner_revenue || 0);
            this.elements.metricOwnerExpenses.textContent = this.formatCurrency(summary.owner_expenses || 0);
            this.elements.metricReviewQueue.textContent = String((workspace?.review_queue || []).length);
            this.elements.metricCorroboration.textContent = `${corroborationState.matched_count || 0} / ${(corroborationState.rows || []).length || 0}`;

            this.elements.reportWorkspaceValue.textContent = workspace ? `${portfolio.name || portfolio.code} · ${period.name}` : 'No workspace selected';
            this.elements.reportReadinessNote.textContent = this.buildReadinessText(workspace);

            this.renderUploads(workspace?.uploads || []);
            this.renderStageUploadControls(workspace?.uploads || []);
            this.renderRevenueChannelConfig();
            this.renderRevisions(workspace?.workspace_revisions || []);
            this.renderChat(workspace?.messages || []);
            this.renderProposals(workspace);

            const sheetViews = workspace?.sheet_views || [];
            if (!sheetViews.length) {
                this.state.activeSheetKey = null;
                this.renderSheetTabs([]);
                this.elements.sheetMetaSummary.textContent = 'Spreadsheet tabs appear here as soon as a month is active.';
                this.elements.sheetGridContainer.innerHTML = '<div class="bk-sheet-empty">Select a portfolio and month to load the live bookkeeping spreadsheet.</div>';
                this.renderStageUploadControls([]);
                this.renderRevenueChannelConfig();
                this.renderRowEditor();
                this.renderEvidencePreview();
                this.updateStepUi();
                return;
            }

            if (!sheetViews.some((view) => view.key === this.state.activeSheetKey)) {
                this.state.activeSheetKey = sheetViews[0].key;
            }

            this.renderSheetTabs(sheetViews);
            this.renderActiveSheet();
            this.renderRowEditor();
            this.renderEvidencePreview();
            this.updateStepUi();
        }

        renderStageUploadControls(uploads) {
            const revenueUploads = (uploads || []).filter((upload) => upload.stage === 'revenue');
            const expenseUploads = (uploads || []).filter((upload) => upload.stage === 'expense');
            const expenseBatch = this.getActiveProcessingBatch('expense');
            const expenseUploadLocked = this.stageHasActiveUpload('expense');
            const revenueChecklist = this.state.workspace?.revenue_checklist || [];
            const missingChannels = revenueChecklist.filter((entry) => entry.expected && entry.missing);

            this.elements.clearRevenueUploadsBtn.disabled = !revenueUploads.length;
            this.elements.clearExpenseUploadsBtn.disabled = !expenseUploads.length || Boolean(expenseBatch);
            if (this.elements.expenseUploadSubmitBtn) {
                this.elements.expenseUploadSubmitBtn.disabled = expenseUploadLocked;
                this.elements.expenseUploadSubmitBtn.textContent = expenseUploadLocked ? 'Processing expense evidence...' : 'Upload expense evidence';
            }
            if (this.elements.expenseFilesInput) {
                this.elements.expenseFilesInput.disabled = expenseUploadLocked;
            }
            if (this.elements.expenseNotesInput) {
                this.elements.expenseNotesInput.disabled = expenseUploadLocked;
            }
            this.elements.clearRevenueUploadsBtn.textContent = revenueUploads.length
                ? `Clear revenue uploads (${revenueUploads.length})`
                : 'Clear revenue uploads';
            this.elements.clearExpenseUploadsBtn.textContent = expenseBatch
                ? `Processing ${expenseBatch.processed_uploads || 0} / ${expenseBatch.total_uploads || 0}`
                : (expenseUploads.length
                    ? `Clear expense uploads (${expenseUploads.length})`
                    : 'Clear expense uploads');
            this.elements.revenueUploadSummary.textContent = revenueUploads.length
                ? `${revenueUploads.length} revenue upload${revenueUploads.length === 1 ? '' : 's'} currently feeding workbook revenue tabs and owner totals.${missingChannels.length ? ` Missing: ${missingChannels.map((entry) => this.channelDisplayLabel(entry.source)).join(', ')}.` : ''}`
                : (missingChannels.length
                    ? `No revenue uploads are currently loaded into this workspace. Missing: ${missingChannels.map((entry) => this.channelDisplayLabel(entry.source)).join(', ')}.`
                    : 'No revenue uploads are currently loaded into this workspace.');
            this.elements.expenseUploadSummary.textContent = expenseBatch
                    ? `${expenseBatch.processed_uploads || 0} of ${expenseBatch.total_uploads || 0} expense file${(expenseBatch.total_uploads || 0) === 1 ? '' : 's'} processed. ${expenseBatch.remaining_uploads || 0} left.${expenseBatch.failed_uploads ? ` ${expenseBatch.failed_uploads} failed.` : ''}`
                    : (expenseUploads.length
                        ? `${expenseUploads.length} expense upload${expenseUploads.length === 1 ? '' : 's'} currently feeding workbook expense tabs and owner totals.`
                        : 'No expense uploads are currently loaded into this workspace.');
            this.renderExpenseStepUploads(expenseUploads, expenseBatch);
        }

        getStatusElementForStage(stage) {
            return ({
                revenue: this.elements.revenueUploadStatus,
                expense: this.elements.expenseUploadStatus,
                corroboration: this.elements.corroborationUploadStatus,
            })[stage] || null;
        }

        getActiveProcessingBatch(stage) {
            return (this.state.workspace?.processing_batches || []).find((batch) => batch.stage === stage && ['queued', 'processing'].includes(batch.status)) || null;
        }

        stageHasActiveUpload(stage) {
            return this.state.pendingUploadStages.has(stage) || Boolean(this.getActiveProcessingBatch(stage));
        }

        getUploadsForStage(stage) {
            return (this.state.workspace?.uploads || []).filter((upload) => upload.stage === stage);
        }

        renderExpenseStepUploads(expenseUploads, expenseBatch) {
            if (!this.elements.expenseStepUploadsList) {
                return;
            }
            const uploads = expenseUploads || [];
            if (!uploads.length) {
                this.elements.expenseStepUploadsList.innerHTML = '<div class="bk-empty">Expense evidence uploaded for this month will appear here.</div>';
                return;
            }

            const selectableUploads = uploads.filter((upload) => !['queued', 'processing'].includes(upload.upload_status));
            const selectedIds = selectableUploads
                .filter((upload) => this.state.selectedUploadIds.has(upload.bookkeeping_upload_id))
                .map((upload) => upload.bookkeeping_upload_id);
            const allSelectableSelected = Boolean(selectableUploads.length) && selectedIds.length === selectableUploads.length;
            const removalLocked = Boolean(expenseBatch);

            const toolbar = `
                <div class="bk-stage-upload-toolbar">
                    <div>
                        <strong>Manage expense evidence</strong>
                        <p>Select the exact files you want to remove. The workbook will refresh automatically after deletion.</p>
                    </div>
                    <div class="bk-inline-actions">
                        <button class="btn btn-secondary" type="button" data-toggle-upload-stage="expense" ${!selectableUploads.length || removalLocked ? 'disabled' : ''}>${allSelectableSelected ? 'Clear selection' : 'Select all'}</button>
                        <button class="btn btn-secondary" type="button" data-remove-selected-stage="expense" ${!selectedIds.length || removalLocked ? 'disabled' : ''}>Remove selected (${selectedIds.length})</button>
                    </div>
                </div>
            `;

            const items = uploads.map((upload) => `
                <div class="bk-stage-upload-item">
                    <div class="bk-stage-upload-item-head">
                        <div style="display:grid;gap:0.35rem;">
                            <strong>${this.escapeHtml(upload.original_filename)}</strong>
                            <span class="bk-upload-badge is-${this.escapeHtml(upload.upload_status || 'stored')}">${this.escapeHtml((upload.upload_status || 'stored').replace(/_/g, ' '))}</span>
                        </div>
                        <input type="checkbox" data-select-upload-id="${upload.bookkeeping_upload_id}" ${this.state.selectedUploadIds.has(upload.bookkeeping_upload_id) ? 'checked' : ''} ${['queued', 'processing'].includes(upload.upload_status) || removalLocked ? 'disabled' : ''}>
                    </div>
                    ${this.buildUploadThumbnailMarkup(upload)}
                    <p>${this.escapeHtml(this.labelForSource(upload.source))}${upload.processing_error ? ` · ${this.escapeHtml(upload.processing_error)}` : ''}</p>
                    <div class="bk-inline-actions">
                        <button class="btn btn-secondary" type="button" data-delete-upload-id="${upload.bookkeeping_upload_id}" ${['queued', 'processing'].includes(upload.upload_status) ? 'disabled' : ''}>Remove</button>
                    </div>
                </div>
            `).join('');

            this.elements.expenseStepUploadsList.innerHTML = toolbar + items;
        }

        buildUploadThumbnailMarkup(upload, options = {}) {
            const { compact = false } = options;
            if (!upload?.bookkeeping_upload_id) {
                return '';
            }
            const previewUrl = `/bookkeeping/api/uploads/${upload.bookkeeping_upload_id}/file`;
            const previewKind = this.resolveUploadPreviewKind(upload);
            const sizeClass = compact ? ' is-compact' : '';
            if (previewKind === 'image') {
                return `
                    <img class="bk-upload-thumbnail${sizeClass}" src="${previewUrl}" alt="${this.escapeHtml(upload.original_filename || 'Upload preview')}" loading="lazy">
                `;
            }
            const placeholderLabel = previewKind === 'pdf' ? 'PDF receipt or statement' : 'Open source file';
            return `
                <a class="bk-upload-thumbnail-button" href="${previewUrl}" target="_blank" rel="noopener noreferrer">
                    <span class="bk-upload-thumbnail-placeholder${sizeClass}">${this.escapeHtml(placeholderLabel)}</span>
                </a>
            `;
        }

        stopProcessingBatchPolling(stage) {
            const active = this.processingPolls[stage];
            if (active?.timer) {
                window.clearTimeout(active.timer);
            }
            delete this.processingPolls[stage];
        }

        stopAllProcessingBatchPolling() {
            Object.keys(this.processingPolls).forEach((stage) => this.stopProcessingBatchPolling(stage));
        }

        syncProcessingBatchPolling(workspace) {
            const activeStages = new Set();
            for (const batch of workspace?.processing_batches || []) {
                if (!['queued', 'processing'].includes(batch.status)) {
                    continue;
                }
                activeStages.add(batch.stage);
                this.startProcessingBatchPolling(batch);
            }
            Object.keys(this.processingPolls).forEach((stage) => {
                if (!activeStages.has(stage)) {
                    this.stopProcessingBatchPolling(stage);
                }
            });
        }

        renderProcessingBatchStatus(stage, batch) {
            const element = this.getStatusElementForStage(stage);
            if (!element || !batch) {
                return;
            }
            const total = Number(batch.total_uploads || 0);
            const processed = Number(batch.processed_uploads || 0);
            const remaining = Math.max(0, Number(batch.remaining_uploads ?? (total - processed)));
            const failed = Number(batch.failed_uploads || 0);
            const currentFilename = batch.current_filename ? ` Working on ${batch.current_filename}.` : '';
            const isTerminal = ['completed', 'completed_with_errors', 'failed'].includes(batch.status);

            let title = 'Queued for processing';
            let meta = `${processed} of ${total} processed. ${remaining} left.${currentFilename}`;
            let error = false;
            if (batch.status === 'processing') {
                title = 'Processing uploaded files';
                meta = `${processed} of ${total} processed. ${remaining} left.${failed ? ` ${failed} failed.` : ''}${currentFilename}`;
            } else if (batch.status === 'completed') {
                title = 'Processing complete';
                meta = `${processed} of ${total} processed. The workbook is up to date.`;
            } else if (batch.status === 'completed_with_errors') {
                title = 'Processing complete with issues';
                meta = `${processed} of ${total} processed. ${failed} failed and need attention.`;
                error = true;
            } else if (batch.status === 'failed') {
                title = 'Processing failed';
                meta = batch.error_message || `The batch stopped before finishing. ${failed || total} file${(failed || total) === 1 ? '' : 's'} failed.`;
                error = true;
            }

            this.setUploadStatus(element, {
                title,
                meta,
                progress: isTerminal ? 100 : Number(batch.progress_percent || 0),
                error,
            });
        }

        startProcessingBatchPolling(batch) {
            if (!batch?.bookkeeping_processing_batch_id || !batch?.stage) {
                return;
            }
            const stage = batch.stage;
            const batchId = Number(batch.bookkeeping_processing_batch_id);
            const current = this.processingPolls[stage];
            this.renderProcessingBatchStatus(stage, batch);
            if (current?.batchId === batchId) {
                return;
            }
            this.stopProcessingBatchPolling(stage);
            this.processingPolls[stage] = { batchId, timer: null };

            const poll = async () => {
                try {
                    const data = await this.fetchJson(`/bookkeeping/api/processing-batches/${batchId}`);
                    const latestBatch = data.processing_batch;
                    this.renderProcessingBatchStatus(stage, latestBatch);
                    if (['completed', 'completed_with_errors', 'failed'].includes(latestBatch.status)) {
                        this.stopProcessingBatchPolling(stage);
                        await this.refreshWorkspace();
                        this.renderProcessingBatchStatus(stage, latestBatch);
                        return;
                    }
                    if (this.processingPolls[stage]?.batchId === batchId) {
                        this.processingPolls[stage].timer = window.setTimeout(poll, 1200);
                    }
                } catch (error) {
                    this.stopProcessingBatchPolling(stage);
                    this.setUploadStatus(this.getStatusElementForStage(stage), {
                        title: 'Processing status unavailable',
                        meta: error.message || 'Failed to read the processing status from Cotton Candy.',
                        progress: 0,
                        error: true,
                    });
                }
            };

            this.processingPolls[stage].timer = window.setTimeout(poll, 1200);
        }

        renderPortfolioQuickSwitch() {
            if (!this.elements.portfolioQuickSwitch) return;
            const options = ['<option value="">Choose portfolio</option>'].concat(
                this.state.portfolios.map((portfolio) => `
                        <option value="${portfolio.bookkeeping_portfolio_id}" ${portfolio.bookkeeping_portfolio_id === this.state.selectedPortfolioId ? 'selected' : ''}>
                        ${this.escapeHtml(portfolio.name || portfolio.code || `Portfolio ${portfolio.bookkeeping_portfolio_id}`)}
                    </option>
                `),
            );
            this.elements.portfolioQuickSwitch.innerHTML = options.join('');
        }

        renderPeriodQuickSwitch() {
            if (!this.elements.periodQuickSwitch) return;
            if (!this.state.selectedPortfolioId) {
                this.elements.periodQuickSwitch.innerHTML = '<option value="">Choose month</option>';
                return;
            }
            const options = ['<option value="">Choose month</option>'].concat(
                this.state.periods.map((period) => `
                    <option value="${period.bookkeeping_period_id}" ${period.bookkeeping_period_id === this.state.selectedPeriodId ? 'selected' : ''}>
                        ${this.escapeHtml(period.name || `Month ${period.bookkeeping_period_id}`)}
                    </option>
                `),
            );
            this.elements.periodQuickSwitch.innerHTML = options.join('');
        }

        renderSheetTabs(sheetViews) {
            this.elements.sheetTabs.innerHTML = sheetViews.map((sheet) => `
                <button type="button" class="bk-tab ${sheet.key === this.state.activeSheetKey ? 'is-active' : ''}" data-sheet-key="${sheet.key}">
                    ${this.escapeHtml(sheet.label)}
                </button>
            `).join('');
        }

        renderActiveSheet() {
            const sheet = this.getActiveSheet();
            if (!sheet) {
                this.elements.sheetGridContainer.innerHTML = '<div class="bk-sheet-empty">No sheet selected.</div>';
                return;
            }

            this.elements.sheetStatusBadge.textContent = sheet.editable ? 'Editable' : 'Read only';
            this.elements.sheetMeta.textContent = `${sheet.label} · ${(sheet.rows || []).length} row${(sheet.rows || []).length === 1 ? '' : 's'}${sheet.editable ? ' · click a row to edit' : ''}`;
            this.elements.sheetMetaSummary.textContent = `${sheet.label} · ${(sheet.rows || []).length} row${(sheet.rows || []).length === 1 ? '' : 's'}`;
            this.elements.addSheetRowBtn.disabled = !(sheet.key === 'expenses_all' || sheet.key === 'revenue_all');

            if (!(sheet.rows || []).length) {
                this.elements.sheetGridContainer.innerHTML = '<div class="bk-sheet-empty">This sheet does not have any rows yet.</div>';
                return;
            }

            const headers = sheet.columns || [];
            const rowsHtml = (sheet.rows || []).map((row) => {
                const isSelected = this.state.selectedRow
                    && this.state.selectedRow.rowType === row.row_type
                    && this.state.selectedRow.rowId === Number(row.row_id);
                const rowNeedsReview = row.needs_review || row.kind === 'change_proposal' || (row.reason && sheet.key === 'review_queue');
                return `
                    <tr
                        class="${isSelected ? 'is-selected ' : ''}${rowNeedsReview ? 'needs-review' : ''}"
                        ${row.row_id ? `data-row-id="${row.row_id}" data-row-type="${row.row_type || ''}"` : ''}
                    >
                        ${headers.map((column) => `<td>${this.formatCell(row[column.key])}</td>`).join('')}
                    </tr>
                `;
            }).join('');

            this.elements.sheetGridContainer.innerHTML = `
                <table class="bk-sheet-table">
                    <thead>
                        <tr>${headers.map((column) => `<th>${this.escapeHtml(column.label)}</th>`).join('')}</tr>
                    </thead>
                    <tbody>${rowsHtml}</tbody>
                </table>
            `;
        }

        renderUploads(uploads) {
            this.elements.uploadCountBadge.textContent = String(uploads.length);
            if (!uploads.length) {
                this.elements.uploadsList.innerHTML = '<div class="bk-empty">Workspace uploads will appear here.</div>';
                return;
            }

            const toolbar = `
                <div class="bk-upload-item">
                    <strong>Bulk actions</strong>
                    <p>Select uploads to remove stale source files without touching the rest of the workspace.</p>
                    <div class="bk-inline-actions" style="margin-top:0.75rem;">
                        <button class="btn btn-secondary" type="button" data-toggle-all-uploads>Select all</button>
                        <button class="btn btn-secondary" type="button" data-bulk-delete-uploads>Remove selected</button>
                    </div>
                </div>
            `;

            const items = uploads.map((upload) => `
                <div class="bk-upload-item">
                    <div style="display:flex;justify-content:space-between;gap:0.75rem;align-items:start;">
                        <div style="display:grid;gap:0.35rem;">
                            <strong>${this.escapeHtml(upload.original_filename)}</strong>
                            <span class="bk-upload-badge is-${this.escapeHtml(upload.upload_status || 'stored')}">${this.escapeHtml((upload.upload_status || 'stored').replace(/_/g, ' '))}</span>
                        </div>
                        <input type="checkbox" data-select-upload-id="${upload.bookkeeping_upload_id}" ${this.state.selectedUploadIds.has(upload.bookkeeping_upload_id) ? 'checked' : ''} ${['queued', 'processing'].includes(upload.upload_status) ? 'disabled' : ''}>
                    </div>
                    ${this.buildUploadThumbnailMarkup(upload, { compact: true })}
                    <p>${this.escapeHtml(this.labelForStage(upload.stage))} · ${this.escapeHtml(this.labelForSource(upload.source))}${upload.sheet_name ? ` · sheet ${this.escapeHtml(upload.sheet_name)}` : ''}${upload.processing_error ? ` · ${this.escapeHtml(upload.processing_error)}` : ''}</p>
                    <div class="bk-inline-actions" style="margin-top:0.75rem;">
                        <button class="btn btn-secondary" type="button" data-delete-upload-id="${upload.bookkeeping_upload_id}" ${['queued', 'processing'].includes(upload.upload_status) ? 'disabled' : ''}>Remove</button>
                    </div>
                </div>
            `).join('');

            this.elements.uploadsList.innerHTML = toolbar + items;
        }

        renderRevisions(revisions) {
            if (!revisions.length) {
                this.elements.revisionList.innerHTML = '<div class="bk-empty">Approvals and exports will appear here.</div>';
                return;
            }
            this.elements.revisionList.innerHTML = revisions.map((revision) => `
                <div class="bk-list-item">
                    <strong>${this.escapeHtml((revision.status || '').toUpperCase())}</strong>
                    <span>${this.escapeHtml(this.formatDateTime(revision.created_at))}${revision.workbook_filename ? ` · ${this.escapeHtml(revision.workbook_filename)}` : ''}</span>
                </div>
            `).join('');
        }

        renderProposals(workspace) {
            const pendingProposals = (workspace?.change_proposals || []).filter((proposal) => proposal.status === 'pending');
            const reviewRows = (workspace?.review_queue || []).filter((row) => row.kind !== 'change_proposal');
            this.elements.pendingProposalCount.textContent = String(pendingProposals.length + reviewRows.length);

            if (!pendingProposals.length && !reviewRows.length) {
                this.elements.proposalList.innerHTML = '<div class="bk-empty">No pending AI proposals or flagged rows.</div>';
                return;
            }

            const proposalHtml = pendingProposals.map((proposal) => `
                <div class="bk-proposal-item">
                    <strong>Proposal for ${this.escapeHtml((proposal.row_type || '').replace('_', ' '))} #${proposal.row_id}</strong>
                    <p>${this.escapeHtml(proposal.reason || 'AI found a conflicting update.')}</p>
                    <p><strong>Suggested fields:</strong> ${this.escapeHtml(Object.keys(proposal.proposed_values || {}).join(', ') || '—')}</p>
                    <div class="bk-inline-actions" style="margin-top:0.75rem;">
                        <button class="btn btn-primary" type="button" data-proposal-action="accept" data-proposal-id="${proposal.bookkeeping_ai_change_proposal_id}">Accept</button>
                        <button class="btn btn-secondary" type="button" data-proposal-action="reject" data-proposal-id="${proposal.bookkeeping_ai_change_proposal_id}">Reject</button>
                        <button class="btn btn-secondary" type="button" data-open-row-type="${proposal.row_type}" data-open-row-id="${proposal.row_id}">Open row</button>
                    </div>
                </div>
            `).join('');

            const reviewHtml = reviewRows.map((row) => `
                <div class="bk-proposal-item">
                    <strong>${this.escapeHtml(row.label || row.kind)}</strong>
                    <p>${this.escapeHtml(row.reason || 'Needs review')}</p>
                    ${row.row_id ? `<div class="bk-inline-actions" style="margin-top:0.75rem;"><button class="btn btn-secondary" type="button" data-open-row-type="${row.row_type}" data-open-row-id="${row.row_id}">Open row</button></div>` : ''}
                </div>
            `).join('');

            this.elements.proposalList.innerHTML = proposalHtml + reviewHtml;
        }

        renderChat(messages) {
            if (!messages.length) {
                this.elements.chatList.innerHTML = '<div class="bk-empty">Select a workspace to start the conversation.</div>';
                return;
            }
            this.elements.chatList.innerHTML = messages.map((message) => `
                <div class="bk-chat-item ${message.role}">
                    <strong>${this.escapeHtml((message.role || 'assistant').toUpperCase())}</strong>
                    <p>${this.escapeHtml(message.message_text || '')}</p>
                </div>
            `).join('');
        }

        renderRowEditor() {
            const selection = this.state.selectedRow;
            if (!selection) {
                this.elements.selectionBadge.textContent = 'No row selected';
                this.elements.rowEditor.innerHTML = '<div class="bk-empty">Select a revenue or expense row from the spreadsheet to edit it here.</div>';
                return;
            }

            if (selection.rowType === 'expense_item') {
                const item = this.findExpenseItem(selection.rowId);
                if (!item) {
                    this.clearSelection();
                    return;
                }
                this.elements.selectionBadge.textContent = this.state.editorMode === 'create' ? 'New expense row' : `Expense #${selection.rowId}`;
                this.elements.rowEditor.innerHTML = this.buildExpenseEditor(item, this.state.editorMode);
                return;
            }

            if (selection.rowType === 'revenue_item') {
                const item = this.findRevenueItem(selection.rowId);
                if (!item) {
                    this.clearSelection();
                    return;
                }
                this.elements.selectionBadge.textContent = this.state.editorMode === 'create' ? 'New revenue row' : `Revenue #${selection.rowId}`;
                this.elements.rowEditor.innerHTML = this.buildRevenueEditor(item, this.state.editorMode);
                return;
            }

            this.elements.selectionBadge.textContent = 'Read only';
            this.elements.rowEditor.innerHTML = '<div class="bk-empty">This sheet is derived from editable rows elsewhere in the workspace.</div>';
        }

        buildExpenseEditor(item, mode) {
            const categories = (this.state.referenceData?.expense_categories || []).map((entry) => `<option value="${entry.value}" ${entry.value === (item.category || 'misc') ? 'selected' : ''}>${this.escapeHtml(entry.label)}</option>`).join('');
            const mappingOptions = this.buildListingOptions(item.property_code);
            const canApprove = mode !== 'create' && (item.needs_review || item.review_reason);
            return `
                <form id="rowEditorForm" data-row-type="expense_item" data-mode="${mode}" data-item-id="${item.bookkeeping_expense_item_id || ''}" class="bk-inline-editor">
                    <div class="bk-inline-editor-grid">
                        <label>Category
                            <select name="category">${categories}</select>
                        </label>
                        <label>Property
                            <input name="property_code" list="listingOptions" value="${this.escapeHtml(item.property_code || '')}" placeholder="Portfolio-level expenses can stay blank">
                            ${mappingOptions}
                        </label>
                        <label>Item
                            <input name="item_name" value="${this.escapeHtml(item.item_name || '')}">
                        </label>
                        <label>Vendor
                            <input name="vendor" value="${this.escapeHtml(item.vendor || '')}">
                        </label>
                        <label>Service date
                            <input name="service_date" type="date" value="${this.escapeHtml(item.service_date || '')}">
                        </label>
                        <label>Payment date
                            <input name="payment_date" type="date" value="${this.escapeHtml(item.payment_date || '')}">
                        </label>
                        <label>Amount
                            <input name="total" type="number" step="0.01" value="${this.escapeHtml((item.total ?? item.amount ?? item.effective_total ?? '')?.toString() || '')}">
                        </label>
                        <label>Payment method
                            <input name="payment_method" value="${this.escapeHtml(item.payment_method || '')}">
                        </label>
                        <label>Scope
                            <select name="scope">
                                <option value="property" ${item.scope === 'property' ? 'selected' : ''}>Property</option>
                                <option value="portfolio" ${item.scope === 'portfolio' ? 'selected' : ''}>Portfolio</option>
                            </select>
                        </label>
                        <label>Needs review
                            <select name="needs_review">
                                <option value="false" ${!item.needs_review ? 'selected' : ''}>No</option>
                                <option value="true" ${item.needs_review ? 'selected' : ''}>Yes</option>
                            </select>
                        </label>
                    </div>
                    <div class="bk-inline-editor-grid full">
                        <label>Details
                            <textarea name="details">${this.escapeHtml(item.details || item.description || '')}</textarea>
                        </label>
                        <label>Review reason
                            <textarea name="review_reason">${this.escapeHtml(item.review_reason || '')}</textarea>
                        </label>
                        <label>Reason for this manual change
                            <textarea name="edit_note" placeholder="Explain why you changed the row. This note becomes portfolio-specific correction memory." ${mode === 'create' ? '' : 'required'}></textarea>
                        </label>
                    </div>
                    <div class="bk-inline-actions">
                        <button class="btn btn-primary" type="submit">${mode === 'create' ? 'Create expense row' : 'Save expense row'}</button>
                        ${canApprove ? '<button class="btn btn-secondary" type="submit" data-row-approve="true" formnovalidate>Approve and remove from review queue</button>' : ''}
                    </div>
                </form>
            `;
        }

        buildRevenueEditor(item, mode) {
            const sourceOptions = (this.state.referenceData?.revenue_sources || []).map((entry) => `<option value="${entry.value}" ${entry.value === (item.source || '') ? 'selected' : ''}>${this.escapeHtml(entry.label)}</option>`).join('');
            const mappingOptions = this.buildListingOptions(item.property_code);
            const mappingSelectOptions = [`<option value="">None</option>`].concat((this.state.workspace?.listing_mappings || []).map((mapping) => `
                <option value="${mapping.bookkeeping_listing_mapping_id}" ${String(mapping.bookkeeping_listing_mapping_id) === String(item.listing_mapping_id || '') ? 'selected' : ''}>${this.escapeHtml(mapping.official_name)}</option>
            `)).join('');
            const canApprove = mode !== 'create' && (item.needs_review || item.review_reason);

            return `
                <form id="rowEditorForm" data-row-type="revenue_item" data-mode="${mode}" data-item-id="${item.bookkeeping_revenue_item_id || ''}" class="bk-inline-editor">
                    <div class="bk-inline-editor-grid">
                        <label>Source
                            <select name="source">${sourceOptions}</select>
                        </label>
                        <label>Listing mapping
                            <select name="listing_mapping_id">${mappingSelectOptions}</select>
                        </label>
                        <label>Property
                            <input name="property_code" list="listingOptions" value="${this.escapeHtml(item.property_code || '')}">
                            ${mappingOptions}
                        </label>
                        <label>Guest
                            <input name="guest_name" value="${this.escapeHtml(item.guest_name || '')}">
                        </label>
                        <label>Reservation ID
                            <input name="reservation_identifier" value="${this.escapeHtml(item.reservation_identifier || '')}">
                        </label>
                        <label>Confirmation code
                            <input name="confirmation_code" value="${this.escapeHtml(item.confirmation_code || '')}">
                        </label>
                        <label>Start date
                            <input name="start_date" type="date" value="${this.escapeHtml(item.start_date || '')}">
                        </label>
                        <label>End date
                            <input name="end_date" type="date" value="${this.escapeHtml(item.end_date || '')}">
                        </label>
                        <label>Booking date
                            <input name="booking_date" type="date" value="${this.escapeHtml(item.booking_date || '')}">
                        </label>
                        <label>Gross amount
                            <input name="gross_amount" type="number" step="0.01" value="${this.escapeHtml((item.gross_amount ?? '')?.toString() || '')}">
                        </label>
                        <label>Commission
                            <input name="commission_amount" type="number" step="0.01" value="${this.escapeHtml((item.commission_amount ?? '')?.toString() || '')}">
                        </label>
                        <label>Hostaway fee
                            <input name="hostaway_fee_amount" type="number" step="0.01" value="${this.escapeHtml((item.hostaway_fee_amount ?? '')?.toString() || '')}">
                        </label>
                        <label>Stripe fee
                            <input name="stripe_fee_amount" type="number" step="0.01" value="${this.escapeHtml((item.stripe_fee_amount ?? '')?.toString() || '')}">
                        </label>
                        <label>Nights
                            <input name="nights" type="number" step="1" value="${this.escapeHtml((item.nights ?? '')?.toString() || '')}">
                        </label>
                        <label>Needs review
                            <select name="needs_review">
                                <option value="false" ${!item.needs_review ? 'selected' : ''}>No</option>
                                <option value="true" ${item.needs_review ? 'selected' : ''}>Yes</option>
                            </select>
                        </label>
                    </div>
                    <div class="bk-inline-editor-grid full">
                        <label>Details
                            <textarea name="details">${this.escapeHtml(item.details || '')}</textarea>
                        </label>
                        <label>Review reason
                            <textarea name="review_reason">${this.escapeHtml(item.review_reason || '')}</textarea>
                        </label>
                        <label>Reason for this manual change
                            <textarea name="edit_note" placeholder="Explain why you changed the revenue row. This note becomes portfolio-specific correction memory." ${mode === 'create' ? '' : 'required'}></textarea>
                        </label>
                    </div>
                    <div class="bk-inline-actions">
                        <button class="btn btn-primary" type="submit">${mode === 'create' ? 'Create revenue row' : 'Save revenue row'}</button>
                        ${canApprove ? '<button class="btn btn-secondary" type="submit" data-row-approve="true" formnovalidate>Approve and remove from review queue</button>' : ''}
                    </div>
                </form>
            `;
        }

        renderEvidencePreview() {
            const selection = this.state.selectedRow;
            if (!selection || !this.state.workspace) {
                this.elements.evidencePreview.innerHTML = '<div class="bk-empty">Select a row to see its linked upload, preview, and metadata.</div>';
                return;
            }

            let item = null;
            if (selection.rowType === 'expense_item') {
                item = this.findExpenseItem(selection.rowId);
            } else if (selection.rowType === 'revenue_item') {
                item = this.findRevenueItem(selection.rowId);
            }
            if (!item) {
                this.elements.evidencePreview.innerHTML = '<div class="bk-empty">No linked source was found for this row.</div>';
                return;
            }

            const uploadId = item.upload_id || item.statement_upload_id;
            const upload = (this.state.workspace.uploads || []).find((entry) => entry.bookkeeping_upload_id === uploadId);
            if (!upload) {
                const sourcePayload = selection.rowType === 'revenue_item' ? item.normalized_data : item.extraction_data;
                this.elements.evidencePreview.innerHTML = `
                    <div class="bk-upload-item">
                        <strong>Row metadata</strong>
                        <p>${this.escapeHtml(JSON.stringify(sourcePayload || {}, null, 2))}</p>
                    </div>
                `;
                return;
            }

            const previewUrl = `/bookkeeping/api/uploads/${upload.bookkeeping_upload_id}/file`;
            let previewHtml = `
                <div class="bk-upload-item">
                    <strong>${this.escapeHtml(upload.original_filename)}</strong>
                    <p>${this.escapeHtml(this.labelForStage(upload.stage))} · ${this.escapeHtml(this.labelForSource(upload.source))}</p>
                    <div class="bk-inline-actions" style="margin-top:0.75rem;">
                        <a class="btn btn-secondary" href="${previewUrl}" target="_blank" rel="noopener noreferrer">Open file</a>
                    </div>
                </div>
            `;

            const previewKind = this.resolveUploadPreviewKind(upload);
            if (previewKind === 'image') {
                previewHtml += `<img src="${previewUrl}" alt="${this.escapeHtml(upload.original_filename)}">`;
            } else if (previewKind === 'pdf') {
                previewHtml += `<iframe src="${previewUrl}"></iframe>`;
            } else {
                previewHtml += `
                    <div class="bk-upload-item">
                        <strong>Source summary</strong>
                        <p>${this.escapeHtml(JSON.stringify(upload.summary || {}, null, 2))}</p>
                    </div>
                `;
            }

            this.elements.evidencePreview.innerHTML = previewHtml;
        }

        openStepModal() {
            this.state.isStepModalOpen = true;
            this.elements.stepModal.classList.add('is-open');
            this.elements.stepModal.setAttribute('aria-hidden', 'false');
            if (this.elements.stepModalTitle) {
                this.elements.stepModalTitle.textContent = this.stepDefinitions.find((item) => item.id === this.state.currentStep)?.title || 'Current bookkeeping step';
            }
        }

        closeStepModal() {
            this.state.isStepModalOpen = false;
            this.elements.stepModal.classList.remove('is-open');
            this.elements.stepModal.setAttribute('aria-hidden', 'true');
        }

        clearUploadFormState() {
            this.stopAllProcessingBatchPolling();
            this.state.pendingUploadStages = new Set();
            [
                [this.elements.revenueUploadForm, this.elements.revenueUploadStatus],
                [this.elements.expenseUploadForm, this.elements.expenseUploadStatus],
                [this.elements.corroborationUploadForm, this.elements.corroborationUploadStatus],
            ].forEach(([form, status]) => {
                if (form) {
                    form.reset();
                }
                if (status) {
                    status.classList.remove('is-visible');
                    status.innerHTML = '';
                }
            });
        }

        setActiveContextPanel(panelKey) {
            this.state.activeContextPanel = panelKey || 'editor';
            document.querySelectorAll('[data-context-tab]').forEach((button) => {
                button.classList.toggle('is-active', button.dataset.contextTab === this.state.activeContextPanel);
            });
            document.querySelectorAll('[data-context-panel]').forEach((panel) => {
                panel.classList.toggle('is-active', panel.dataset.contextPanel === this.state.activeContextPanel);
            });
        }

        setCurrentStep(step, options = {}) {
            const { openModal = false } = options;
            const nextStep = Math.max(1, Math.min(this.stepDefinitions.length, Number(step) || 1));
            this.state.currentStep = nextStep;
            document.querySelectorAll('[data-step-panel]').forEach((panel) => {
                panel.classList.toggle('is-active', Number(panel.dataset.stepPanel) === nextStep);
            });
            if (openModal) {
                this.openStepModal();
            } else if (this.state.isStepModalOpen && this.elements.stepModalTitle) {
                this.elements.stepModalTitle.textContent = this.stepDefinitions.find((item) => item.id === nextStep)?.title || 'Current bookkeeping step';
            }
            this.updateStepUi();
        }

        goToRelativeStep(delta) {
            this.setCurrentStep(this.state.currentStep + delta, { openModal: this.state.isStepModalOpen });
        }

        getStepStatuses() {
            const workspace = this.state.workspace;
            const uploads = workspace?.uploads || [];
            const revenueUploads = uploads.filter((upload) => upload.stage === 'revenue');
            const expenseUploads = uploads.filter((upload) => upload.stage === 'expense');
            const corroborationUploads = uploads.filter((upload) => upload.stage === 'corroboration');
            const reviewQueue = workspace?.review_queue || [];
            const approved = workspace?.period?.status === 'approved';
            const revisions = workspace?.workspace_revisions || [];

            return [
                { id: 1, completed: Boolean(this.state.selectedPortfolioId), statusLabel: this.state.selectedPortfolioId ? 'Ready' : 'Waiting' },
                { id: 2, completed: Boolean(this.state.selectedPeriodId), statusLabel: this.state.selectedPeriodId ? 'Open' : 'Waiting' },
                { id: 3, completed: revenueUploads.length > 0 || (workspace?.revenue_items || []).length > 0, statusLabel: revenueUploads.length ? `${revenueUploads.length} upload${revenueUploads.length === 1 ? '' : 's'}` : 'Waiting' },
                { id: 4, completed: expenseUploads.length > 0, statusLabel: expenseUploads.length ? `${expenseUploads.length} upload${expenseUploads.length === 1 ? '' : 's'}` : 'Waiting' },
                { id: 5, completed: corroborationUploads.length > 0, statusLabel: corroborationUploads.length ? `${corroborationUploads.length} upload${corroborationUploads.length === 1 ? '' : 's'}` : 'Optional' },
                { id: 6, completed: Boolean(workspace) && reviewQueue.length === 0, statusLabel: reviewQueue.length ? `${reviewQueue.length} review` : (workspace ? 'Clean' : 'Waiting') },
                { id: 7, completed: approved || revisions.length > 0, statusLabel: approved ? 'Approved' : (revisions.length ? 'Exported' : 'Waiting') },
            ];
        }

        getRecommendedStep() {
            if (!this.state.selectedPortfolioId) return 1;
            if (!this.state.selectedPeriodId) return 2;
            if (!(this.state.workspace?.revenue_items || []).length && !(this.state.workspace?.uploads || []).some((upload) => upload.stage === 'revenue')) return 3;
            if (!(this.state.workspace?.uploads || []).some((upload) => upload.stage === 'expense')) return 4;
            if (!(this.state.workspace?.uploads || []).some((upload) => upload.stage === 'corroboration')) return 5;
            if ((this.state.workspace?.review_queue || []).length > 0) return 6;
            return 7;
        }

        updateStepUi() {
            const stepStatuses = this.getStepStatuses();
            const recommendedStep = this.getRecommendedStep();
            const currentDefinition = this.stepDefinitions.find((step) => step.id === this.state.currentStep) || this.stepDefinitions[0];

            this.elements.stepperProgressLabel.textContent = `Step ${currentDefinition.id} of ${this.stepDefinitions.length}`;
            this.elements.stepperCurrentTitle.textContent = currentDefinition.title;
            this.elements.stepperProgressMeta.textContent = currentDefinition.copy;
            this.elements.stepperRecommendedAction.textContent = (this.stepDefinitions.find((step) => step.id === recommendedStep) || currentDefinition).recommendation;
            this.elements.stepperProgressFill.style.width = `${(currentDefinition.id / this.stepDefinitions.length) * 100}%`;
            if (this.elements.stepModalTitle) {
                this.elements.stepModalTitle.textContent = currentDefinition.title;
            }
            this.elements.stepBackBtn.disabled = currentDefinition.id === 1;
            this.elements.stepNextBtn.disabled = currentDefinition.id === this.stepDefinitions.length;

            this.elements.stepperNav.querySelectorAll('[data-step-target]').forEach((button) => {
                const stepId = Number(button.dataset.stepTarget);
                const status = stepStatuses.find((entry) => entry.id === stepId);
                button.classList.toggle('is-active', stepId === this.state.currentStep);
                button.classList.toggle('is-complete', Boolean(status?.completed));
                const statusLabel = button.querySelector('.bk-step-status');
                if (statusLabel) {
                    statusLabel.textContent = status?.statusLabel || 'Waiting';
                }
            });
        }

        async handlePortfolioSubmit(event) {
            event.preventDefault();
            const formData = new FormData(this.elements.portfolioForm);
            const payload = Object.fromEntries(formData.entries());
            const isUpdate = Boolean(this.state.selectedPortfolioId);
            const preservedPeriodId = isUpdate ? this.state.selectedPeriodId : null;
            const url = isUpdate ? `/bookkeeping/api/portfolios/${this.state.selectedPortfolioId}` : '/bookkeeping/api/portfolios';
            const method = isUpdate ? 'PUT' : 'POST';
            const data = await this.fetchJson(url, {
                method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            await this.loadPortfolios();
            const portfolio = data.portfolio;
            this.populatePortfolioForm(portfolio);
            await this.selectPortfolio(portfolio.bookkeeping_portfolio_id, {
                autoStep: false,
                preserveSelectedPeriodId: preservedPeriodId,
            });
        }

        populatePortfolioForm(portfolio) {
            document.getElementById('portfolioNameInput').value = portfolio?.name || '';
            this.populateTagOptions(portfolio?.portfolio_tag || portfolio?.listing_tag || '');
            document.getElementById('portfolioManagementFeeInput').value = portfolio?.management_fee_percentage ?? '';
            document.getElementById('portfolioPropertyAddressInput').value = portfolio?.property_address || '';
            document.getElementById('portfolioNotesInput').value = portfolio?.notes || '';
            document.getElementById('hostawayPerListingInput').value = portfolio?.hostaway_price_per_listing ?? '';
            document.getElementById('pricelabsPerListingInput').value = portfolio?.pricelabs_price_per_listing ?? '';
        }

        resetPortfolioForm() {
            this.state.selectedPortfolioId = null;
            this.state.selectedPeriodId = null;
            this.state.periods = [];
            this.state.workspace = null;
            this.state.listingMappings = [];
            this.state.isEditingMappings = false;
            this.state.isEditingRevenueChannels = false;
            this.state.selectedRow = null;
            this.state.activeSheetKey = null;
            this.elements.portfolioForm.reset();
            this.populateTagOptions('');
            this.clearUploadFormState();
            this.renderPortfolioList();
            this.renderPeriodList();
            this.renderListingMappings();
            this.renderWorkspace(null);
            this.setCurrentStep(1);
        }

        async selectPortfolio(portfolioId, options = {}) {
            const { autoStep = true, preserveSelectedPeriodId = null } = options;
            this.state.selectedPortfolioId = portfolioId;
            this.state.selectedRow = null;
            this.state.activeSheetKey = null;
            this.state.selectedUploadIds = new Set();
            this.state.isEditingMappings = false;
            this.state.isEditingRevenueChannels = false;
            this.clearUploadFormState();
            if (!preserveSelectedPeriodId) {
                this.state.selectedPeriodId = null;
                this.state.periods = [];
                this.state.workspace = null;
            }
            const portfolio = this.state.portfolios.find((entry) => entry.bookkeeping_portfolio_id === portfolioId);
            this.populatePortfolioForm(portfolio);
            if (!preserveSelectedPeriodId) {
                this.renderWorkspace(null);
            }
            await Promise.all([
                this.loadPeriods(portfolioId),
                this.loadListingMappings(portfolioId),
            ]);
            if (preserveSelectedPeriodId && this.state.periods.some((period) => period.bookkeeping_period_id === preserveSelectedPeriodId)) {
                this.state.selectedPeriodId = preserveSelectedPeriodId;
                this.renderPeriodList();
                await this.refreshWorkspace();
            }
            this.renderPortfolioList();
            if (autoStep) {
                this.setCurrentStep(2);
            }
        }

        async handlePeriodSubmit(event) {
            event.preventDefault();
            if (!this.state.selectedPortfolioId) {
                window.alert('Select a portfolio first.');
                return;
            }

            const formData = new FormData(this.elements.periodForm);
            const payload = Object.fromEntries(formData.entries());
            payload.portfolio_id = this.state.selectedPortfolioId;
            const data = await this.fetchJson('/bookkeeping/api/periods', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            await this.loadPeriods(this.state.selectedPortfolioId);
            await this.selectPeriod(data.period.bookkeeping_period_id, { autoStep: false });
        }

        async selectPeriod(periodId, options = {}) {
            const { autoStep = true } = options;
            this.state.selectedPeriodId = periodId;
            this.state.selectedUploadIds = new Set();
            this.state.selectedRow = null;
            this.state.editorMode = 'update';
            this.clearUploadFormState();
            this.renderPeriodList();
            await this.refreshWorkspace();
            if (autoStep) {
                this.setCurrentStep(this.getRecommendedStep());
            }
        }

        async saveListingMappings() {
            if (!this.state.selectedPortfolioId) {
                window.alert('Select a portfolio first.');
                return;
            }

            const rows = Array.from(this.elements.listingMappingTableBody.querySelectorAll('[data-listing-row]'));
            const mappings = rows
                .filter((row) => row.querySelector('[data-mapping-active]')?.checked)
                .map((row) => ({
                    listing_id: Number(row.dataset.listingId),
                    official_name: row.querySelector('[data-mapping-official]')?.value?.trim(),
                    aliases: row.querySelector('[data-mapping-aliases]')?.value?.split(',').map((alias) => alias.trim()).filter(Boolean) || [],
                    is_active: true,
                }));

            await this.fetchJson(`/bookkeeping/api/portfolios/${this.state.selectedPortfolioId}/listing-mappings`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mappings }),
            });
            this.state.isEditingMappings = false;
            await this.loadPortfolios();
            await this.loadListingMappings(this.state.selectedPortfolioId);
            if (this.state.selectedPeriodId) {
                await this.refreshWorkspace();
            }
        }

        async saveRevenueChannels() {
            if (!this.state.selectedPortfolioId) {
                window.alert('Select a portfolio first.');
                return;
            }
            const selectedChannels = Array.from(this.elements.revenueChannelsConfig.querySelectorAll('[data-revenue-channel]:checked'))
                .map((input) => input.value);
            if (!selectedChannels.length) {
                window.alert('Select at least one expected revenue channel for this portfolio.');
                return;
            }

            const data = await this.fetchJson(`/bookkeeping/api/portfolios/${this.state.selectedPortfolioId}/revenue-channels`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ channels: selectedChannels }),
            });
            if (data.portfolio) {
                this.upsertPortfolioState(data.portfolio);
                this.populatePortfolioForm(data.portfolio);
            }
            this.state.isEditingRevenueChannels = false;
            if (this.state.selectedPeriodId) {
                await this.refreshWorkspace();
            } else {
                this.renderRevenueChannelConfig();
            }
        }

        startCreateRow() {
            const activeSheet = this.getActiveSheet();
            if (!activeSheet) return;
            if (activeSheet.key === 'expenses_all') {
                this.state.selectedRow = {
                    rowType: 'expense_item',
                    rowId: null,
                };
                this.state.editorMode = 'create';
                this.renderRowEditor();
                return;
            }
            if (activeSheet.key === 'revenue_all') {
                this.state.selectedRow = {
                    rowType: 'revenue_item',
                    rowId: null,
                };
                this.state.editorMode = 'create';
                this.elements.rowEditor.innerHTML = this.buildRevenueEditor({ source: 'airbnb', normalized_data: {} }, 'create');
                this.elements.selectionBadge.textContent = 'New revenue row';
                return;
            }
            window.alert('New rows can be added from the All Expenses or All Revenue tabs.');
        }

        sheetKeyForRowType(rowType) {
            if (rowType === 'expense_item') return 'expenses_all';
            if (rowType === 'revenue_item') return 'revenue_all';
            return null;
        }

        focusRowEditor() {
            const editorCard = this.elements.rowEditor?.closest('.bk-context-card');
            if (editorCard) {
                editorCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
            const firstEditableField = this.elements.rowEditor?.querySelector('input:not([type="hidden"]), select, textarea');
            if (firstEditableField) {
                firstEditableField.focus({ preventScroll: true });
            }
        }

        openRowInEditor(rowType, rowId, options = {}) {
            const { switchSheet = false, closeModal = false, focusEditor = false } = options;
            const targetSheetKey = switchSheet ? this.sheetKeyForRowType(rowType) : null;
            if (targetSheetKey && this.state.activeSheetKey !== targetSheetKey) {
                this.setActiveSheet(targetSheetKey);
            }
            this.selectRow(rowType, rowId);
            if (closeModal && this.state.isStepModalOpen) {
                this.closeStepModal();
            }
            if (focusEditor) {
                window.setTimeout(() => this.focusRowEditor(), closeModal ? 180 : 0);
            }
        }

        selectRow(rowType, rowId) {
            this.state.selectedRow = { rowType, rowId };
            this.state.editorMode = 'update';
            this.setActiveContextPanel('editor');
            this.renderActiveSheet();
            this.renderRowEditor();
            this.renderEvidencePreview();
        }

        clearSelection() {
            this.state.selectedRow = null;
            this.state.editorMode = 'update';
            this.renderActiveSheet();
            this.renderRowEditor();
            this.renderEvidencePreview();
        }

        async handleRowEditorSubmit(event) {
            event.preventDefault();
            const form = event.target.closest('#rowEditorForm');
            if (!form) return;
            const rowType = form.dataset.rowType;
            const mode = form.dataset.mode;
            const isApproval = event.submitter?.dataset.rowApprove === 'true';
            const formData = new FormData(form);
            const payload = {};
            for (const [key, value] of formData.entries()) {
                payload[key] = value;
            }
            if (isApproval) {
                payload.needs_review = false;
                payload.review_reason = '';
                if (!(payload.edit_note || '').trim()) {
                    payload.edit_note = rowType === 'expense_item'
                        ? 'Operator approved the expense review item and cleared the review flag.'
                        : 'Operator approved the revenue review item and cleared the review flag.';
                }
            }
            payload.needs_review = payload.needs_review === 'true';

            if (rowType === 'expense_item') {
                await this.saveExpenseRow(mode, form.dataset.itemId, payload);
            } else if (rowType === 'revenue_item') {
                await this.saveRevenueRow(mode, form.dataset.itemId, payload);
            }
        }

        async saveExpenseRow(mode, itemId, payload) {
            if (!this.state.selectedPeriodId) return;
            const method = mode === 'create' ? 'POST' : 'PUT';
            const url = mode === 'create'
                ? `/bookkeeping/api/periods/${this.state.selectedPeriodId}/expense-items`
                : `/bookkeeping/api/expense-items/${itemId}`;
            if (mode === 'update') {
                const item = this.findExpenseItem(Number(itemId));
                payload.updated_at = item?.updated_at || '';
            }
            const data = await this.fetchJson(url, {
                method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            await this.refreshWorkspace();
            if (mode === 'create' && data.expense_item) {
                this.selectRow('expense_item', data.expense_item.bookkeeping_expense_item_id);
            } else if (mode === 'update') {
                this.selectRow('expense_item', Number(itemId));
            }
        }

        async saveRevenueRow(mode, itemId, payload) {
            if (!this.state.selectedPeriodId) return;
            const method = mode === 'create' ? 'POST' : 'PUT';
            const url = mode === 'create'
                ? `/bookkeeping/api/periods/${this.state.selectedPeriodId}/revenue-items`
                : `/bookkeeping/api/revenue-items/${itemId}`;
            if (mode === 'update') {
                const item = this.findRevenueItem(Number(itemId));
                payload.updated_at = item?.updated_at || '';
            }
            const data = await this.fetchJson(url, {
                method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            await this.refreshWorkspace();
            if (mode === 'create' && data.revenue_item) {
                this.selectRow('revenue_item', data.revenue_item.bookkeeping_revenue_item_id);
            } else if (mode === 'update') {
                this.selectRow('revenue_item', Number(itemId));
            }
        }

        handleEditorDependentChange(event) {
            if (event.target.name !== 'listing_mapping_id') return;
            const mappingId = Number(event.target.value);
            const mapping = (this.state.workspace?.listing_mappings || []).find((entry) => entry.bookkeeping_listing_mapping_id === mappingId);
            if (!mapping) return;
            const propertyInput = event.target.form?.querySelector('[name="property_code"]');
            if (propertyInput && !propertyInput.value.trim()) {
                propertyInput.value = mapping.official_name || '';
            }
        }

        async handleAssistantSubmit(event) {
            event.preventDefault();
            if (!this.state.selectedPeriodId) {
                window.alert('Select a month workspace first.');
                return;
            }
            const message = this.elements.assistantMessageInput.value.trim();
            if (!message) {
                window.alert('Enter a message for the bookkeeping copilot.');
                return;
            }
            this.elements.assistantMessageInput.value = '';
            this.setActiveContextPanel('copilot');
            await this.fetchJson(`/bookkeeping/api/periods/${this.state.selectedPeriodId}/assistant/messages`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message }),
            });
            await this.refreshWorkspace();
        }

        async handleRevenueUpload(event) {
            event.preventDefault();
            await this.submitUploadForm(this.elements.revenueUploadForm, this.elements.revenueUploadStatus, 'revenue');
        }

        async handleExpenseUpload(event) {
            event.preventDefault();
            await this.submitUploadForm(this.elements.expenseUploadForm, this.elements.expenseUploadStatus, 'expense');
        }

        async handleCorroborationUpload(event) {
            event.preventDefault();
            await this.submitUploadForm(this.elements.corroborationUploadForm, this.elements.corroborationUploadStatus, 'corroboration');
        }

        async submitUploadForm(form, statusElement, stage) {
            if (!this.state.selectedPeriodId) {
                window.alert('Select a month workspace first.');
                return;
            }
            if (this.stageHasActiveUpload(stage)) {
                window.alert(`Cotton Candy is still processing the current ${stage} upload batch. Wait for it to finish before uploading more files.`);
                return;
            }

            const formData = new FormData(form);
            const files = formData.getAll('files');
            if (!files.length || !files[0] || !files[0].name) {
                window.alert('Choose at least one file to upload.');
                return;
            }
            formData.set('stage', stage);
            this.state.pendingUploadStages.add(stage);
            this.renderStageUploadControls(this.state.workspace?.uploads || []);

            await new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open('POST', `/bookkeeping/api/periods/${this.state.selectedPeriodId}/uploads`);
                const releasePendingStage = () => {
                    this.state.pendingUploadStages.delete(stage);
                    this.renderStageUploadControls(this.state.workspace?.uploads || []);
                };

                this.setUploadStatus(statusElement, {
                    title: 'Uploading files',
                    meta: 'Sending files to Cotton Candy and waiting for bookkeeping normalization.',
                    progress: 3,
                });

                xhr.upload.onprogress = (event) => {
                    if (!event.lengthComputable) return;
                    const percent = Math.max(3, Math.round((event.loaded / event.total) * 92));
                    this.setUploadStatus(statusElement, {
                        title: 'Uploading files',
                        meta: `${files.length} file${files.length === 1 ? '' : 's'} in flight`,
                        progress: percent,
                    });
                };

                xhr.upload.onload = () => {
                    this.setUploadStatus(statusElement, {
                        title: 'Processing uploaded files',
                        meta: 'Cotton Candy received the files and is extracting bookkeeping rows on the server.',
                        progress: 95,
                    });
                };

                xhr.onload = async () => {
                    try {
                        let responseData = {};
                        try {
                            responseData = JSON.parse(xhr.responseText || '{}');
                        } catch (error) {
                            responseData = {};
                        }
                        if (xhr.status === 409 && responseData.processing_batch) {
                            form.reset();
                            this.startProcessingBatchPolling(responseData.processing_batch);
                            await this.refreshWorkspace();
                            releasePendingStage();
                            resolve(responseData);
                            return;
                        }
                        if (xhr.status < 200 || xhr.status >= 300) {
                            const errorMessage = responseData.error || 'Upload failed';
                            this.setUploadStatus(statusElement, { title: 'Upload failed', meta: errorMessage, progress: 0, error: true });
                            releasePendingStage();
                            reject(new Error(errorMessage));
                            return;
                        }
                        if (xhr.status === 202 && responseData.processing_batch) {
                            form.reset();
                            this.startProcessingBatchPolling(responseData.processing_batch);
                            await this.refreshWorkspace();
                            releasePendingStage();
                            resolve(responseData);
                            return;
                        }
                        this.setUploadStatus(statusElement, {
                            title: 'Processing uploaded files',
                            meta: 'The workspace is being refreshed with new spreadsheet rows.',
                            progress: 100,
                        });
                        await this.refreshWorkspace();
                        form.reset();
                        this.setUploadStatus(statusElement, {
                            title: 'Upload complete',
                            meta: 'The live spreadsheet has been refreshed.',
                            progress: 100,
                        });
                        releasePendingStage();
                        resolve(responseData);
                    } catch (error) {
                        releasePendingStage();
                        this.setUploadStatus(statusElement, {
                            title: 'Upload failed',
                            meta: error.message || 'The upload finished, but the workspace could not refresh.',
                            progress: 0,
                            error: true,
                        });
                        reject(error);
                    }
                };

                xhr.onerror = () => {
                    this.setUploadStatus(statusElement, { title: 'Upload failed', meta: 'Network error while uploading files.', progress: 0, error: true });
                    releasePendingStage();
                    reject(new Error('Network error while uploading files'));
                };

                xhr.send(formData);
            });
        }

        setUploadStatus(element, { title, meta, progress, error = false }) {
            if (!element) {
                return;
            }
            element.classList.add('is-visible');
            element.innerHTML = `
                <strong>${this.escapeHtml(title)}</strong>
                <div>${this.escapeHtml(meta || '')}</div>
                <div class="bk-upload-status-track">
                    <div class="bk-upload-status-fill" style="width:${Math.max(0, Math.min(100, progress || 0))}%;${error ? 'background:#dc2626;' : ''}"></div>
                </div>
            `;
        }

        async reprocessExpenseEvidence() {
            if (!this.state.selectedPeriodId) {
                window.alert('Select a month workspace first.');
                return;
            }
            if (!window.confirm('Reprocess all expense evidence for this month? Human-edited rows will not be overwritten; conflicts will turn into review proposals.')) {
                return;
            }
            await this.fetchJson(`/bookkeeping/api/periods/${this.state.selectedPeriodId}/reprocess-expenses`, { method: 'POST' });
            await this.refreshWorkspace();
        }

        async approveWorkspace() {
            if (!this.state.selectedPeriodId) {
                window.alert('Select a month workspace first.');
                return;
            }
            try {
                await this.fetchJson(`/bookkeeping/api/periods/${this.state.selectedPeriodId}/approve`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ force: false }),
                });
            } catch (error) {
                if (!window.confirm(`${error.message}\n\nDo you want to force approval anyway?`)) {
                    return;
                }
                await this.fetchJson(`/bookkeeping/api/periods/${this.state.selectedPeriodId}/approve`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ force: true }),
                });
            }
            await this.refreshWorkspace();
        }

        async deleteSelectedPortfolio() {
            if (!this.state.selectedPortfolioId) {
                window.alert('Select a portfolio first.');
                return;
            }
            const portfolio = this.getSelectedPortfolio();
            const portfolioName = portfolio?.name || 'this portfolio';
            const confirmed = window.confirm(
                `Delete ${portfolioName}?\n\nThis will permanently delete all bookkeeping information for this portfolio across every month, including uploaded revenue files, expense evidence, revenue rows, expense rows, reviews, and exports. This cannot be undone.`
            );
            if (!confirmed) {
                return;
            }

            await this.fetchJson(`/bookkeeping/api/portfolios/${this.state.selectedPortfolioId}`, {
                method: 'DELETE',
            });
            await this.loadPortfolios();
            this.resetPortfolioForm();
        }

        async exportWorkbook() {
            if (!this.state.selectedPeriodId) {
                window.alert('Select a month workspace first.');
                return;
            }
            if (this.state.isExporting) {
                return;
            }
            const driveStatus = this.state.driveStatus || {};
            if ((driveStatus.effective_mode || driveStatus.mode) === 'not_connected' && !driveStatus.service_account_fallback_available) {
                const confirmed = window.confirm(
                    'Google Drive is not connected for this Cotton Candy account yet, so evidence sync will be skipped.\n\nDo you want to continue downloading the workbook anyway?'
                );
                if (!confirmed) {
                    return;
                }
            }
            try {
                this.setExportState(
                    true,
                    'Preparing workbook',
                    'Cotton Candy is syncing evidence to Drive, generating the workbook, and the file will download automatically when ready.'
                );
                const response = await fetch(`/bookkeeping/api/periods/${this.state.selectedPeriodId}/export`);
                const contentType = response.headers.get('content-type') || '';
                if (!response.ok) {
                    let message = `Export failed (${response.status})`;
                    if (contentType.includes('application/json')) {
                        const data = await response.json();
                        message = data.error || message;
                    } else {
                        const text = await response.text();
                        if (text) {
                            message = text;
                        }
                    }
                    throw new Error(message);
                }

                const blob = await response.blob();
                this.setExportState(
                    true,
                    'Starting download',
                    'The workbook is ready. Your browser should begin downloading it automatically.'
                );
                const disposition = response.headers.get('content-disposition') || '';
                const filenameMatch = disposition.match(/filename\*=UTF-8''([^;]+)|filename=\"?([^\";]+)\"?/i);
                const filename = decodeURIComponent(filenameMatch?.[1] || filenameMatch?.[2] || 'bookkeeping-workbook.xlsx');
                const downloadUrl = window.URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = downloadUrl;
                link.download = filename;
                document.body.appendChild(link);
                link.click();
                link.remove();
                window.URL.revokeObjectURL(downloadUrl);
            } catch (error) {
                window.alert(error.message || 'Failed to export the workbook.');
            } finally {
                this.setExportState(false);
            }
        }

        handleProposalListClick(event) {
            const proposalButton = event.target.closest('[data-proposal-action]');
            if (proposalButton) {
                this.resolveProposal(Number(proposalButton.dataset.proposalId), proposalButton.dataset.proposalAction);
                return;
            }
            const openRowButton = event.target.closest('[data-open-row-type][data-open-row-id]');
            if (openRowButton) {
                this.openRowInEditor(openRowButton.dataset.openRowType, Number(openRowButton.dataset.openRowId), {
                    switchSheet: true,
                    closeModal: true,
                    focusEditor: true,
                });
            }
        }

        async resolveProposal(proposalId, action) {
            await this.fetchJson(`/bookkeeping/api/change-proposals/${proposalId}/resolve`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action }),
            });
            await this.refreshWorkspace();
        }

        handleUploadListClick(event) {
            const toggleStageButton = event.target.closest('[data-toggle-upload-stage]');
            if (toggleStageButton) {
                this.toggleStageUploadSelection(toggleStageButton.dataset.toggleUploadStage);
                return;
            }
            const removeSelectedStageButton = event.target.closest('[data-remove-selected-stage]');
            if (removeSelectedStageButton) {
                this.removeSelectedUploadsForStage(removeSelectedStageButton.dataset.removeSelectedStage);
                return;
            }
            const deleteButton = event.target.closest('[data-delete-upload-id]');
            if (deleteButton) {
                this.deleteUpload(Number(deleteButton.dataset.deleteUploadId));
                return;
            }
            const previewButton = event.target.closest('[data-preview-upload-id]');
            if (previewButton) {
                this.previewUpload(Number(previewButton.dataset.previewUploadId));
                return;
            }
            const toggleAllButton = event.target.closest('[data-toggle-all-uploads]');
            if (toggleAllButton) {
                const uploads = this.state.workspace?.uploads || [];
                const allSelected = uploads.length && uploads.every((upload) => this.state.selectedUploadIds.has(upload.bookkeeping_upload_id));
                this.state.selectedUploadIds = allSelected
                    ? new Set()
                    : new Set(uploads.map((upload) => upload.bookkeeping_upload_id));
                this.renderUploads(uploads);
                return;
            }
            const bulkDeleteButton = event.target.closest('[data-bulk-delete-uploads]');
            if (bulkDeleteButton) {
                this.bulkDeleteUploads();
            }
        }

        handleUploadListChange(event) {
            const checkbox = event.target.closest('[data-select-upload-id]');
            if (!checkbox) return;
            const uploadId = Number(checkbox.dataset.selectUploadId);
            if (checkbox.checked) {
                this.state.selectedUploadIds.add(uploadId);
            } else {
                this.state.selectedUploadIds.delete(uploadId);
            }
            this.renderUploads(this.state.workspace?.uploads || []);
            this.renderStageUploadControls(this.state.workspace?.uploads || []);
        }

        async deleteUpload(uploadId) {
            if (!window.confirm('Remove this upload from the workspace? Linked revenue rows are deleted. Auto-created expense rows are deleted, and manual expense rows are unlinked and flagged for review.')) {
                return;
            }
            await this.fetchJson(`/bookkeeping/api/uploads/${uploadId}`, { method: 'DELETE' });
            this.state.selectedUploadIds.delete(uploadId);
            await this.refreshWorkspace();
        }

        async bulkDeleteUploads() {
            const uploadIds = Array.from(this.state.selectedUploadIds);
            if (!uploadIds.length) {
                window.alert('Select at least one upload.');
                return;
            }
            if (!window.confirm(`Remove ${uploadIds.length} selected upload${uploadIds.length === 1 ? '' : 's'} from the workspace?`)) {
                return;
            }
            await this.fetchJson('/bookkeeping/api/uploads/bulk-delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ upload_ids: uploadIds }),
            });
            this.state.selectedUploadIds = new Set();
            await this.refreshWorkspace();
        }

        toggleStageUploadSelection(stage) {
            const selectableUploads = this.getUploadsForStage(stage).filter((upload) => !['queued', 'processing'].includes(upload.upload_status));
            if (!selectableUploads.length) {
                return;
            }
            const uploadIds = selectableUploads.map((upload) => upload.bookkeeping_upload_id);
            const allSelected = uploadIds.every((uploadId) => this.state.selectedUploadIds.has(uploadId));
            if (allSelected) {
                for (const uploadId of uploadIds) {
                    this.state.selectedUploadIds.delete(uploadId);
                }
            } else {
                for (const uploadId of uploadIds) {
                    this.state.selectedUploadIds.add(uploadId);
                }
            }
            this.renderUploads(this.state.workspace?.uploads || []);
            this.renderStageUploadControls(this.state.workspace?.uploads || []);
        }

        async removeSelectedUploadsForStage(stage) {
            const uploadIds = this.getUploadsForStage(stage)
                .filter((upload) => this.state.selectedUploadIds.has(upload.bookkeeping_upload_id))
                .filter((upload) => !['queued', 'processing'].includes(upload.upload_status))
                .map((upload) => upload.bookkeeping_upload_id);
            if (!uploadIds.length) {
                window.alert(`Select at least one ${stage} upload.`);
                return;
            }
            if (!window.confirm(`Remove ${uploadIds.length} selected ${stage} upload${uploadIds.length === 1 ? '' : 's'} from the workspace? The spreadsheet will refresh automatically.`)) {
                return;
            }
            await this.fetchJson('/bookkeeping/api/uploads/bulk-delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ upload_ids: uploadIds }),
            });
            for (const uploadId of uploadIds) {
                this.state.selectedUploadIds.delete(uploadId);
            }
            await this.refreshWorkspace();
        }

        previewUpload(uploadId) {
            const upload = (this.state.workspace?.uploads || []).find((entry) => entry.bookkeeping_upload_id === uploadId);
            if (!upload) return;
            this.setActiveContextPanel('evidence');
            const previewUrl = `/bookkeeping/api/uploads/${uploadId}/file`;
            let previewHtml = `
                <div class="bk-upload-item">
                    <strong>${this.escapeHtml(upload.original_filename)}</strong>
                    <p>${this.escapeHtml(this.labelForStage(upload.stage))} · ${this.escapeHtml(this.labelForSource(upload.source))}</p>
                    <div class="bk-inline-actions" style="margin-top:0.75rem;">
                        <a class="btn btn-secondary" href="${previewUrl}" target="_blank" rel="noopener noreferrer">Open file</a>
                    </div>
                </div>
            `;
            const previewKind = this.resolveUploadPreviewKind(upload);
            if (previewKind === 'image') {
                previewHtml += `<img src="${previewUrl}" alt="${this.escapeHtml(upload.original_filename)}">`;
            } else if (previewKind === 'pdf') {
                previewHtml += `<iframe src="${previewUrl}"></iframe>`;
            } else if (upload.summary) {
                previewHtml += `
                    <div class="bk-upload-item">
                        <strong>Source summary</strong>
                        <p>${this.escapeHtml(JSON.stringify(upload.summary || {}, null, 2))}</p>
                    </div>
                `;
            }
            this.elements.evidencePreview.innerHTML = previewHtml;
        }

        async clearUploadsByStage(stage) {
            if (!this.state.selectedPeriodId || !this.state.workspace) {
                window.alert('Select a month workspace first.');
                return;
            }
            const uploads = (this.state.workspace.uploads || []).filter((upload) => upload.stage === stage);
            if (!uploads.length) {
                window.alert(`There are no ${stage} uploads to clear in this workspace.`);
                return;
            }
            const confirmation = stage === 'revenue'
                ? 'Clear all revenue uploads for this month? This also removes their derived revenue rows from the workbook.'
                : 'Clear all expense uploads for this month? Auto-created expense rows will be deleted and manual rows will be unlinked for review.';
            if (!window.confirm(confirmation)) {
                return;
            }
            await this.fetchJson('/bookkeeping/api/uploads/bulk-delete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ upload_ids: uploads.map((upload) => upload.bookkeeping_upload_id) }),
            });
            this.state.selectedUploadIds = new Set();
            this.clearUploadFormState();
            await this.refreshWorkspace();
        }

        resolveUploadPreviewKind(upload) {
            const contentType = String(upload?.content_type || '').toLowerCase();
            const fileExtension = String(upload?.file_extension || '').toLowerCase();
            const filename = String(upload?.original_filename || '').toLowerCase();
            const extension = fileExtension || (filename.includes('.') ? `.${filename.split('.').pop()}` : '');
            if (contentType === 'application/pdf' || extension === '.pdf') {
                return 'pdf';
            }
            if (
                contentType.startsWith('image/')
                || ['.jpg', '.jpeg', '.jfif', '.png', '.webp'].includes(extension)
            ) {
                return 'image';
            }
            return 'other';
        }

        findExpenseItem(itemId) {
            if (!itemId) {
                return {
                    category: 'misc',
                    scope: 'portfolio',
                    needs_review: false,
                };
            }
            return (this.state.workspace?.expense_items || []).find((item) => item.bookkeeping_expense_item_id === Number(itemId));
        }

        findRevenueItem(itemId) {
            if (!itemId) {
                return {
                    source: 'airbnb',
                    needs_review: false,
                    normalized_data: {},
                };
            }
            return (this.state.workspace?.revenue_items || []).find((item) => item.bookkeeping_revenue_item_id === Number(itemId));
        }

        getActiveSheet() {
            return (this.state.workspace?.sheet_views || []).find((sheet) => sheet.key === this.state.activeSheetKey) || null;
        }

        setActiveSheet(sheetKey) {
            this.state.activeSheetKey = sheetKey;
            this.renderSheetTabs(this.state.workspace?.sheet_views || []);
            this.renderActiveSheet();
        }

        buildListingOptions(selectedValue) {
            const options = (this.state.workspace?.listing_mappings || []).map((mapping) => `<option value="${this.escapeHtml(mapping.official_name)}"></option>`).join('');
            return `<datalist id="listingOptions">${options}</datalist>`;
        }

        buildReadinessText(workspace) {
            if (!workspace) {
                return 'Select a workspace first. The workbook will be exported from the live spreadsheet state.';
            }
            const pending = workspace.summary_cards?.pending_change_proposals || 0;
            const reviewRows = (workspace.review_queue || []).length;
            if ((workspace.period?.status || '') === 'approved') {
                return 'The workspace is approved. Export will snapshot the current spreadsheet state.';
            }
            if (pending || reviewRows) {
                return `There are ${reviewRows} review item${reviewRows === 1 ? '' : 's'} and ${pending} pending proposal${pending === 1 ? '' : 's'}. Resolve them before approving.`;
            }
            return 'The spreadsheet looks clean. Approve the workspace before exporting for a clean audit trail.';
        }

        labelForSource(source) {
            const reference = (this.state.referenceData?.revenue_sources || []).find((entry) => entry.value === source)
                || (this.state.referenceData?.special_upload_sources || []).find((entry) => entry.value === source);
            return reference?.label || source.replace(/_/g, ' ');
        }

        labelForStage(stage) {
            return ({
                revenue: 'Revenue',
                expense: 'Expense evidence',
                corroboration: 'Corroboration',
            })[stage] || stage;
        }

        formatCell(value) {
            if (value === null || value === undefined || value === '') return '—';
            if (typeof value === 'boolean') return value ? 'Yes' : 'No';
            if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2);
            return this.escapeHtml(String(value)).replace(/\n/g, '<br>');
        }

        formatCurrency(value) {
            return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value || 0));
        }

        formatDateTime(value) {
            if (!value) return '—';
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) return value;
            return date.toLocaleString();
        }

        escapeHtml(value) {
            return String(value ?? '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        }
    }

    document.addEventListener('DOMContentLoaded', async () => {
        const workspace = new BookkeepingWorkspace();
        window.bookkeepingWorkspace = workspace;
        try {
            await workspace.init();
        } catch (error) {
            console.error(error);
            window.alert(error.message || 'Failed to initialize the bookkeeping workspace.');
        }
    });
})();
