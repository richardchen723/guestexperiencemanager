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
                sheetSelection: {
                    rowType: null,
                    rowIds: new Set(),
                    cellField: null,
                    anchorRowId: null,
                },
                editorMode: 'update',
                selectedUploadIds: new Set(),
                pendingUploadStages: new Set(),
                activeContextPanel: 'editor',
                isStepModalOpen: false,
                isEditingMappings: false,
                isEditingRevenueChannels: false,
                driveStatus: null,
                isExporting: false,
                bulkFeedback: null,
                activeReceiptUploadId: null,
                pendingReceiptUploadIds: [],
                receiptReviewTotal: 0,
                receiptReviewCompleted: 0,
                generatedReceiptFilename: null,
                isReceiptEditorOpen: false,
                driveFolderStack: [],
                isDriveUploadDialogOpen: false,
                isUploadingReceiptsToDrive: false,
                isCreatingDriveFolder: false,
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
                stepModalFooter: document.getElementById('stepModalFooter'),
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
                expenseFilePrompt: document.getElementById('expenseFilePrompt'),
                expenseNotesInput: document.getElementById('expenseNotesInput'),
                clearRevenueUploadsBtn: document.getElementById('clearRevenueUploadsBtn'),
                clearExpenseUploadsBtn: document.getElementById('clearExpenseUploadsBtn'),
                uploadReceiptsToDriveBtn: document.getElementById('uploadReceiptsToDriveBtn'),
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
                sheetSelectionBar: document.getElementById('sheetSelectionBar'),
                sheetSelectionTitle: document.getElementById('sheetSelectionTitle'),
                sheetSelectionHint: document.getElementById('sheetSelectionHint'),
                selectAllSheetRowsBtn: document.getElementById('selectAllSheetRowsBtn'),
                clearSheetSelectionBtn: document.getElementById('clearSheetSelectionBtn'),
                sheetSelectionLive: document.getElementById('sheetSelectionLive'),
                rowEditorTitle: document.getElementById('rowEditorTitle'),
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
                receiptOrganizerWorkspace: document.getElementById('receiptOrganizerWorkspace'),
                receiptOrganizerProgress: document.getElementById('receiptOrganizerProgress'),
                receiptOrganizerCloseBtn: document.getElementById('receiptOrganizerCloseBtn'),
                receiptOrganizerForm: document.getElementById('receiptOrganizerForm'),
                receiptOrganizerOriginalName: document.getElementById('receiptOrganizerOriginalName'),
                receiptOrganizerPreview: document.getElementById('receiptOrganizerPreview'),
                receiptOrganizerDate: document.getElementById('receiptOrganizerDate'),
                receiptOrganizerType: document.getElementById('receiptOrganizerType'),
                receiptOrganizerExpenseTypeField: document.getElementById('receiptOrganizerExpenseTypeField'),
                receiptOrganizerExpenseType: document.getElementById('receiptOrganizerExpenseType'),
                receiptOrganizerStoreField: document.getElementById('receiptOrganizerStoreField'),
                receiptOrganizerStore: document.getElementById('receiptOrganizerStore'),
                receiptOrganizerFilename: document.getElementById('receiptOrganizerFilename'),
                receiptOrganizerFilenamePreview: document.getElementById('receiptOrganizerFilenamePreview'),
                receiptOrganizerPathPreview: document.getElementById('receiptOrganizerPathPreview'),
                receiptOrganizerReviewNote: document.getElementById('receiptOrganizerReviewNote'),
                receiptOrganizerSaveBtn: document.getElementById('receiptOrganizerSaveBtn'),
                receiptOrganizerNextBtn: document.getElementById('receiptOrganizerNextBtn'),
                driveUploadDialog: document.getElementById('driveUploadDialog'),
                driveUploadCloseBtn: document.getElementById('driveUploadCloseBtn'),
                driveUploadCancelBtn: document.getElementById('driveUploadCancelBtn'),
                driveUploadSubmitBtn: document.getElementById('driveUploadSubmitBtn'),
                driveUploadReadiness: document.getElementById('driveUploadReadiness'),
                driveUploadStatus: document.getElementById('driveUploadStatus'),
                driveUploadReconnectBtn: document.getElementById('driveUploadReconnectBtn'),
                driveUploadOpenFolderLink: document.getElementById('driveUploadOpenFolderLink'),
                driveFolderBackBtn: document.getElementById('driveFolderBackBtn'),
                driveFolderBreadcrumbs: document.getElementById('driveFolderBreadcrumbs'),
                driveFolderList: document.getElementById('driveFolderList'),
                driveNewFolderBtn: document.getElementById('driveNewFolderBtn'),
                driveFolderCreateForm: document.getElementById('driveFolderCreateForm'),
                driveFolderNameInput: document.getElementById('driveFolderNameInput'),
                driveFolderCreateBtn: document.getElementById('driveFolderCreateBtn'),
                driveFolderCreateCancelBtn: document.getElementById('driveFolderCreateCancelBtn'),
                driveSelectedFolderLabel: document.getElementById('driveSelectedFolderLabel'),
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
            this.elements.uploadReceiptsToDriveBtn.addEventListener('click', () => this.openDriveUploadDialog());
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

            this.elements.receiptOrganizerCloseBtn.addEventListener('click', () => this.closeReceiptOrganizer());
            this.elements.receiptOrganizerForm.addEventListener('submit', (event) => {
                event.preventDefault();
                this.saveReceiptOrganization({ advance: false });
            });
            this.elements.receiptOrganizerSaveBtn.addEventListener('click', () => this.saveReceiptOrganization({ advance: false }));
            this.elements.receiptOrganizerNextBtn.addEventListener('click', () => this.nextReceiptOrganization());
            this.elements.receiptOrganizerType.addEventListener('change', () => this.updateReceiptOrganizerPreview());
            [
                this.elements.receiptOrganizerDate,
                this.elements.receiptOrganizerExpenseType,
                this.elements.receiptOrganizerStore,
            ].forEach((input) => input.addEventListener('input', () => this.updateReceiptOrganizerPreview()));
            this.elements.receiptOrganizerFilename.addEventListener('input', () => this.updateReceiptOrganizerDestination());
            this.elements.expenseFilesInput.addEventListener('change', () => this.handleExpenseFileSelection());
            this.elements.driveUploadCloseBtn.addEventListener('click', () => this.closeDriveUploadDialog());
            this.elements.driveUploadCancelBtn.addEventListener('click', () => this.closeDriveUploadDialog());
            this.elements.driveUploadSubmitBtn.addEventListener('click', () => this.uploadApprovedReceiptsToDrive());
            this.elements.driveUploadReconnectBtn.addEventListener('click', () => this.connectGoogleDrive());
            this.elements.driveFolderBackBtn.addEventListener('click', () => this.goBackOneDriveFolder());
            this.elements.driveNewFolderBtn.addEventListener('click', () => this.showDriveFolderCreateForm());
            this.elements.driveFolderCreateCancelBtn.addEventListener('click', () => this.hideDriveFolderCreateForm());
            this.elements.driveFolderCreateForm.addEventListener('submit', (event) => {
                event.preventDefault();
                this.createDriveFolder();
            });
            this.elements.driveFolderList.addEventListener('click', (event) => {
                const folderButton = event.target.closest('[data-drive-folder-id]');
                if (folderButton) {
                    this.openDriveFolder(folderButton.dataset.driveFolderId, folderButton.dataset.driveFolderName);
                }
            });
            this.elements.driveFolderBreadcrumbs.addEventListener('click', (event) => {
                const crumb = event.target.closest('[data-drive-crumb-index]');
                if (crumb && !crumb.disabled) {
                    this.openDriveBreadcrumb(Number(crumb.dataset.driveCrumbIndex));
                }
            });
            this.elements.driveUploadDialog.addEventListener('click', (event) => {
                if (event.target === this.elements.driveUploadDialog) {
                    this.closeDriveUploadDialog();
                }
            });

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
                if (event.key === 'Escape' && this.state.isDriveUploadDialogOpen) {
                    this.closeDriveUploadDialog();
                    return;
                }
                if (event.key === 'Escape' && this.state.isReceiptEditorOpen) {
                    this.closeReceiptOrganizer();
                    return;
                }
                if (event.key === 'Escape' && this.state.isStepModalOpen) {
                    this.closeStepModal();
                    return;
                }
                if (event.key === 'Escape' && this.getSelectedRowIds().length) {
                    this.clearSelection();
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

            this.elements.sheetGridContainer.addEventListener('click', (event) => this.handleSheetGridClick(event));
            this.elements.sheetGridContainer.addEventListener('keydown', (event) => {
                if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'a') {
                    event.preventDefault();
                    this.selectAllSheetRows();
                    return;
                }
                this.handleSheetGridKeydown(event);
            });
            this.elements.selectAllSheetRowsBtn.addEventListener('click', () => this.selectAllSheetRows());
            this.elements.clearSheetSelectionBtn.addEventListener('click', () => this.clearSelection());

            this.elements.rowEditor.addEventListener('submit', (event) => this.handleRowEditorSubmit(event));
            this.elements.rowEditor.addEventListener('change', (event) => this.handleEditorDependentChange(event));
            this.elements.rowEditor.addEventListener('click', (event) => {
                if (event.target.closest('[data-clear-sheet-selection]')) {
                    this.clearSelection();
                }
            });
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

        approvedReceiptUploads() {
            return (this.state.workspace?.uploads || []).filter((upload) => {
                const organization = this.receiptOrganizationForUpload(upload);
                return upload.stage === 'expense'
                    && !['queued', 'processing', 'failed'].includes(upload.upload_status)
                    && organization.status === 'approved'
                    && Boolean(organization.effective_filename);
            });
        }

        expenseReceiptsEligibleForReview() {
            return (this.state.workspace?.uploads || []).filter((upload) => (
                upload.stage === 'expense' && upload.upload_status !== 'failed'
            ));
        }

        renderDriveUploadReadiness() {
            const approvedCount = this.approvedReceiptUploads().length;
            const receiptCount = this.expenseReceiptsEligibleForReview().length;
            const incompleteCount = Math.max(0, receiptCount - approvedCount);
            const account = this.state.driveStatus?.display_name || this.state.driveStatus?.google_email || 'the connected account';
            this.elements.driveUploadReadiness.innerHTML = `
                <strong>${approvedCount} approved receipt${approvedCount === 1 ? '' : 's'} ready</strong>
                <span>${this.escapeHtml(account)} · ${incompleteCount ? `${incompleteCount} incomplete receipt${incompleteCount === 1 ? '' : 's'} will remain in Cotton Candy.` : 'Every processed receipt has an approved file name.'}</span>
            `;
            if (!this.state.isUploadingReceiptsToDrive) {
                const hasDestination = this.state.driveFolderStack.length > 0;
                this.elements.driveUploadSubmitBtn.disabled = approvedCount === 0 || !hasDestination;
                this.elements.driveUploadSubmitBtn.textContent = approvedCount && hasDestination
                    ? `Upload ${approvedCount} receipt${approvedCount === 1 ? '' : 's'} here`
                    : (approvedCount ? 'Choose a folder' : 'No approved receipts');
            }
            this.elements.driveNewFolderBtn.disabled = !this.state.driveFolderStack.length
                || this.state.isCreatingDriveFolder
                || this.state.isUploadingReceiptsToDrive;
        }

        async openDriveUploadDialog() {
            if (!this.state.selectedPeriodId || !this.state.workspace) {
                window.alert('Select a month workspace before uploading receipts to Google Drive.');
                return;
            }
            if ((this.state.driveStatus?.effective_mode || this.state.driveStatus?.mode) !== 'user_authorized') {
                this.connectGoogleDrive();
                return;
            }
            this.state.isDriveUploadDialogOpen = true;
            this.state.driveFolderStack = [];
            this.hideDriveFolderCreateForm();
            this.elements.driveUploadDialog.hidden = false;
            this.elements.driveUploadStatus.textContent = '';
            this.elements.driveUploadStatus.classList.remove('is-error');
            this.elements.driveUploadOpenFolderLink.hidden = true;
            this.elements.driveUploadReconnectBtn.hidden = true;
            this.renderDriveUploadReadiness();
            await this.loadDriveFolder('root', [{ id: 'root', name: 'My Drive' }]);
            this.elements.driveUploadCloseBtn.focus({ preventScroll: true });
        }

        closeDriveUploadDialog() {
            if (this.state.isUploadingReceiptsToDrive || this.state.isCreatingDriveFolder) return;
            this.state.isDriveUploadDialogOpen = false;
            this.state.driveFolderStack = [];
            this.hideDriveFolderCreateForm();
            this.elements.driveUploadDialog.hidden = true;
        }

        async loadDriveFolder(folderId, nextStack) {
            this.hideDriveFolderCreateForm();
            this.elements.driveFolderList.innerHTML = '<div class="bk-empty">Loading your folders…</div>';
            this.elements.driveFolderBackBtn.disabled = true;
            this.elements.driveNewFolderBtn.disabled = true;
            this.elements.driveUploadSubmitBtn.disabled = true;
            this.elements.driveUploadStatus.textContent = 'Loading Google Drive folders…';
            this.elements.driveUploadStatus.classList.remove('is-error');
            try {
                const data = await this.fetchJson(`/bookkeeping/api/google-drive/folders?parent_id=${encodeURIComponent(folderId)}`);
                const stack = nextStack.map((entry) => ({ ...entry }));
                if (stack.length) {
                    stack[stack.length - 1].name = data.current_folder?.name || stack[stack.length - 1].name;
                }
                this.state.driveFolderStack = stack;
                this.renderDriveFolderBrowser(data.folders || []);
                this.elements.driveUploadStatus.textContent = '';
                this.elements.driveUploadReconnectBtn.hidden = true;
                return true;
            } catch (error) {
                const needsReconnect = /reconnect|authorization/i.test(error.message || '');
                this.elements.driveFolderList.innerHTML = `<div class="bk-empty">${needsReconnect ? 'Folder access is unavailable until Google Drive is reconnected.' : 'This folder could not be loaded.'}</div>`;
                this.elements.driveUploadStatus.textContent = error.message || 'Could not load this folder.';
                this.elements.driveUploadStatus.classList.add('is-error');
                this.elements.driveUploadReconnectBtn.hidden = !needsReconnect;
                this.renderDriveFolderPath();
                return false;
            } finally {
                this.renderDriveUploadReadiness();
            }
        }

        renderDriveFolderBrowser(folders) {
            this.renderDriveFolderPath();
            this.elements.driveFolderList.innerHTML = folders.length
                ? folders.map((folder) => `
                    <button
                        class="bk-drive-folder"
                        type="button"
                        data-drive-folder-id="${this.escapeHtml(folder.id)}"
                        data-drive-folder-name="${this.escapeHtml(folder.name || 'Untitled folder')}"
                    >
                        <span class="bk-drive-folder-icon" aria-hidden="true">▰</span>
                        <strong>${this.escapeHtml(folder.name || 'Untitled folder')}</strong>
                        <span class="bk-drive-folder-arrow" aria-hidden="true">›</span>
                    </button>
                `).join('')
                : '<div class="bk-empty">This folder has no subfolders. You can upload here.</div>';
        }

        renderDriveFolderPath() {
            const stack = this.state.driveFolderStack;
            this.elements.driveFolderBackBtn.disabled = stack.length <= 1;
            this.elements.driveNewFolderBtn.disabled = !stack.length
                || this.state.isCreatingDriveFolder
                || this.state.isUploadingReceiptsToDrive;
            this.elements.driveFolderBreadcrumbs.innerHTML = stack.map((entry, index) => `
                ${index ? '<span aria-hidden="true">›</span>' : ''}
                <button class="bk-drive-crumb" type="button" data-drive-crumb-index="${index}" ${index === stack.length - 1 ? 'disabled' : ''}>${this.escapeHtml(entry.name)}</button>
            `).join('');
            this.elements.driveSelectedFolderLabel.textContent = stack.length
                ? `${stack.map((entry) => entry.name).join(' / ')} selected`
                : 'Choose a folder';
        }

        async openDriveFolder(folderId, folderName) {
            const nextStack = [
                ...this.state.driveFolderStack,
                { id: folderId, name: folderName || 'Untitled folder' },
            ];
            await this.loadDriveFolder(folderId, nextStack);
        }

        async openDriveBreadcrumb(index) {
            const nextStack = this.state.driveFolderStack.slice(0, index + 1);
            const target = nextStack[nextStack.length - 1];
            if (target) {
                await this.loadDriveFolder(target.id, nextStack);
            }
        }

        async goBackOneDriveFolder() {
            if (this.state.driveFolderStack.length <= 1) return;
            await this.openDriveBreadcrumb(this.state.driveFolderStack.length - 2);
        }

        showDriveFolderCreateForm() {
            if (!this.state.driveFolderStack.length || this.state.isCreatingDriveFolder) return;
            this.elements.driveFolderCreateForm.hidden = false;
            this.elements.driveFolderNameInput.value = '';
            this.elements.driveUploadStatus.textContent = '';
            this.elements.driveUploadStatus.classList.remove('is-error');
            this.elements.driveFolderNameInput.focus({ preventScroll: true });
        }

        hideDriveFolderCreateForm() {
            this.elements.driveFolderCreateForm.hidden = true;
            this.elements.driveFolderNameInput.value = '';
        }

        async createDriveFolder() {
            const parentFolder = this.state.driveFolderStack[this.state.driveFolderStack.length - 1];
            const folderName = this.elements.driveFolderNameInput.value.trim();
            if (!parentFolder || !folderName || this.state.isCreatingDriveFolder) {
                if (!folderName) this.elements.driveFolderNameInput.reportValidity();
                return;
            }

            this.state.isCreatingDriveFolder = true;
            this.elements.driveFolderCreateBtn.disabled = true;
            this.elements.driveFolderCreateCancelBtn.disabled = true;
            this.elements.driveNewFolderBtn.disabled = true;
            this.elements.driveUploadSubmitBtn.disabled = true;
            this.elements.driveFolderCreateBtn.textContent = 'Creating…';
            this.elements.driveUploadStatus.textContent = `Creating “${folderName}”…`;
            this.elements.driveUploadStatus.classList.remove('is-error');
            try {
                const data = await this.fetchJson('/bookkeeping/api/google-drive/folders', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ parent_id: parentFolder.id, name: folderName }),
                });
                const folder = data.folder;
                const opened = folder?.id && await this.loadDriveFolder(folder.id, [
                    ...this.state.driveFolderStack,
                    { id: folder.id, name: folder.name || folderName },
                ]);
                if (opened) {
                    this.elements.driveUploadStatus.textContent = `Created “${folder.name || folderName}”. This new folder is selected.`;
                }
            } catch (error) {
                this.elements.driveUploadStatus.textContent = error.message || 'Could not create this folder.';
                this.elements.driveUploadStatus.classList.add('is-error');
                this.elements.driveFolderNameInput.focus({ preventScroll: true });
            } finally {
                this.state.isCreatingDriveFolder = false;
                this.elements.driveFolderCreateBtn.disabled = false;
                this.elements.driveFolderCreateCancelBtn.disabled = false;
                this.elements.driveFolderCreateBtn.textContent = 'Create folder';
                this.renderDriveUploadReadiness();
                this.renderDriveFolderPath();
            }
        }

        async uploadApprovedReceiptsToDrive() {
            const targetFolder = this.state.driveFolderStack[this.state.driveFolderStack.length - 1];
            const approvedCount = this.approvedReceiptUploads().length;
            if (!targetFolder || !approvedCount || this.state.isUploadingReceiptsToDrive) return;

            this.state.isUploadingReceiptsToDrive = true;
            this.elements.driveUploadSubmitBtn.disabled = true;
            this.elements.driveUploadSubmitBtn.textContent = 'Uploading receipts…';
            this.elements.driveUploadCloseBtn.disabled = true;
            this.elements.driveUploadCancelBtn.disabled = true;
            this.elements.driveUploadStatus.textContent = `Uploading ${approvedCount} approved receipt${approvedCount === 1 ? '' : 's'} with confirmed file names…`;
            this.elements.driveUploadStatus.classList.remove('is-error');
            this.elements.driveUploadOpenFolderLink.hidden = true;
            try {
                const data = await this.fetchJson(`/bookkeeping/api/periods/${this.state.selectedPeriodId}/receipts/upload-to-google-drive`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ folder_id: targetFolder.id }),
                });
                await this.refreshWorkspace();
                const receiptCount = data.drive_sync?.receipt_count || data.approved_receipt_count || approvedCount;
                this.elements.driveUploadStatus.textContent = `${receiptCount} receipt${receiptCount === 1 ? '' : 's'} uploaded with approved names. The Drive folder structure is ready.`;
                const folderUrl = data.drive_sync?.portfolio_folder_url;
                if (folderUrl) {
                    this.elements.driveUploadOpenFolderLink.href = folderUrl;
                    this.elements.driveUploadOpenFolderLink.hidden = false;
                }
            } catch (error) {
                this.elements.driveUploadStatus.textContent = error.message || 'Could not upload the receipts to Google Drive.';
                this.elements.driveUploadStatus.classList.add('is-error');
            } finally {
                this.state.isUploadingReceiptsToDrive = false;
                this.elements.driveUploadCloseBtn.disabled = false;
                this.elements.driveUploadCancelBtn.disabled = false;
                this.renderDriveUploadReadiness();
            }
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
                this.state.selectedRow = null;
                this.resetSheetSelectionState();
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
                this.state.selectedRow = null;
                this.resetSheetSelectionState();
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
            const expenseUploadLocked = this.stageHasActiveUpload('expense') || this.state.isReceiptEditorOpen;
            const revenueChecklist = this.state.workspace?.revenue_checklist || [];
            const missingChannels = revenueChecklist.filter((entry) => entry.expected && entry.missing);

            this.elements.clearRevenueUploadsBtn.disabled = !revenueUploads.length;
            this.elements.clearExpenseUploadsBtn.disabled = !expenseUploads.length || Boolean(expenseBatch);
            this.elements.uploadReceiptsToDriveBtn.disabled = !expenseUploads.length || Boolean(expenseBatch);
            if (this.elements.expenseUploadSubmitBtn) {
                this.elements.expenseUploadSubmitBtn.disabled = expenseUploadLocked;
                this.elements.expenseUploadSubmitBtn.textContent = this.stageHasActiveUpload('expense')
                    ? 'Analyzing receipts...'
                    : (this.state.isReceiptEditorOpen ? 'Finish this review batch first' : 'Analyze receipts');
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
            const receiptOrganizations = expenseUploads
                .map((upload) => upload.summary?.receipt_organization)
                .filter(Boolean);
            const receiptReadyCount = receiptOrganizations.filter((organization) => ['suggested', 'approved'].includes(organization.status)).length;
            const receiptReviewCount = receiptOrganizations.filter((organization) => organization.status === 'needs_review').length;
            this.elements.revenueUploadSummary.textContent = revenueUploads.length
                ? `${revenueUploads.length} revenue upload${revenueUploads.length === 1 ? '' : 's'} currently feeding workbook revenue tabs and owner totals.${missingChannels.length ? ` Missing: ${missingChannels.map((entry) => this.channelDisplayLabel(entry.source)).join(', ')}.` : ''}`
                : (missingChannels.length
                    ? `No revenue uploads are currently loaded into this workspace. Missing: ${missingChannels.map((entry) => this.channelDisplayLabel(entry.source)).join(', ')}.`
                    : 'No revenue uploads are currently loaded into this workspace.');
            this.elements.expenseUploadSummary.textContent = expenseBatch
                    ? `${expenseBatch.processed_uploads || 0} of ${expenseBatch.total_uploads || 0} expense file${(expenseBatch.total_uploads || 0) === 1 ? '' : 's'} processed. ${expenseBatch.remaining_uploads || 0} left.${expenseBatch.failed_uploads ? ` ${expenseBatch.failed_uploads} failed.` : ''}`
                    : (expenseUploads.length
                        ? `${expenseUploads.length} expense upload${expenseUploads.length === 1 ? '' : 's'} · ${receiptReadyCount} ready to file${receiptReviewCount ? ` · ${receiptReviewCount} need${receiptReviewCount === 1 ? 's' : ''} details` : ''}.`
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

        receiptOrganizationForUpload(upload) {
            const organization = upload?.summary?.receipt_organization;
            return organization && typeof organization === 'object' ? organization : {};
        }

        receiptStatusMeta(upload, organization) {
            if (['queued', 'processing'].includes(upload.upload_status)) {
                return { label: 'Reading receipt', className: '' };
            }
            if (organization.status === 'approved') {
                return { label: 'Approved', className: ' is-approved' };
            }
            if (organization.status === 'needs_review') {
                return { label: 'Needs details', className: ' is-needs-review' };
            }
            if (organization.status === 'suggested') {
                return { label: 'Ready to review', className: '' };
            }
            return { label: upload.upload_status === 'failed' ? 'Processing failed' : 'Not organized', className: ' is-needs-review' };
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

            const items = uploads.map((upload) => {
                const organization = this.receiptOrganizationForUpload(upload);
                const statusMeta = this.receiptStatusMeta(upload, organization);
                const displayName = organization.effective_filename || organization.suggested_filename || 'Cotton Candy is preparing a file name';
                const path = organization.relative_path || 'Destination appears after receipt analysis';
                const missingCopy = (organization.missing_fields || []).length
                    ? `Missing: ${organization.missing_fields.join(', ')}`
                    : (upload.processing_error || 'Service date and receipt context stay editable.');
                const reviewDisabled = ['queued', 'processing'].includes(upload.upload_status);
                return `
                    <div class="bk-stage-upload-item bk-receipt-card">
                        ${this.buildUploadThumbnailMarkup(upload)}
                        <div class="bk-receipt-card-main">
                            <div class="bk-receipt-card-topline">
                                <span class="bk-receipt-status${statusMeta.className}">${this.escapeHtml(statusMeta.label)}</span>
                                <span class="bk-receipt-original">Original: ${this.escapeHtml(upload.original_filename)}</span>
                            </div>
                            <div class="bk-receipt-name">${this.escapeHtml(displayName)}</div>
                            <div class="bk-receipt-path">${this.escapeHtml(path)}</div>
                            <div class="bk-receipt-original">${this.escapeHtml(missingCopy)}</div>
                        </div>
                        <div class="bk-receipt-card-actions">
                            <label class="bk-receipt-original"><input type="checkbox" data-select-upload-id="${upload.bookkeeping_upload_id}" ${this.state.selectedUploadIds.has(upload.bookkeeping_upload_id) ? 'checked' : ''} ${['queued', 'processing'].includes(upload.upload_status) || removalLocked ? 'disabled' : ''}> Select</label>
                            <button class="btn btn-primary" type="button" data-review-receipt-id="${upload.bookkeeping_upload_id}" ${reviewDisabled ? 'disabled' : ''}>${organization.status === 'approved' ? 'Edit name' : 'Review name'}</button>
                            <button class="btn btn-secondary" type="button" data-delete-upload-id="${upload.bookkeeping_upload_id}" ${['queued', 'processing'].includes(upload.upload_status) ? 'disabled' : ''}>Remove</button>
                        </div>
                    </div>
                `;
            }).join('');

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

        setPendingReceiptUploads(uploads) {
            const uploadIds = (uploads || [])
                .map((upload) => Number(upload?.bookkeeping_upload_id))
                .filter(Boolean);
            if (!uploadIds.length || this.state.pendingReceiptUploadIds.length) {
                return;
            }
            this.state.pendingReceiptUploadIds = [...new Set(uploadIds)];
            this.state.receiptReviewTotal = this.state.pendingReceiptUploadIds.length;
            this.state.receiptReviewCompleted = 0;
        }

        startProcessingBatchPolling(batch) {
            if (!batch?.bookkeeping_processing_batch_id || !batch?.stage) {
                return;
            }
            const stage = batch.stage;
            const batchId = Number(batch.bookkeeping_processing_batch_id);
            if (stage === 'expense') {
                this.setPendingReceiptUploads(batch.uploads);
            }
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
                    if (stage === 'expense') {
                        this.setPendingReceiptUploads(latestBatch.uploads);
                    }
                    this.renderProcessingBatchStatus(stage, latestBatch);
                    if (['completed', 'completed_with_errors', 'failed'].includes(latestBatch.status)) {
                        this.stopProcessingBatchPolling(stage);
                        await this.refreshWorkspace();
                        this.renderProcessingBatchStatus(stage, latestBatch);
                        if (stage === 'expense') {
                            this.openPendingReceiptAfterProcessing();
                        }
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

        getSelectedRowIds() {
            return Array.from(this.state.sheetSelection?.rowIds || []);
        }

        getSelectableSheetRows() {
            return (this.getActiveSheet()?.rows || []).filter((row) => row.row_id && row.row_type);
        }

        resetSheetSelectionState() {
            this.state.sheetSelection = {
                rowType: null,
                rowIds: new Set(),
                cellField: null,
                anchorRowId: null,
            };
        }

        reconcileSheetSelection() {
            const selection = this.state.sheetSelection;
            if (!selection?.rowIds?.size) return;
            const availableRows = this.getSelectableSheetRows();
            const availableIds = new Set(
                availableRows
                    .filter((row) => row.row_type === selection.rowType)
                    .map((row) => Number(row.row_id)),
            );
            selection.rowIds = new Set(this.getSelectedRowIds().filter((rowId) => availableIds.has(rowId)));
            if (!selection.rowIds.size) {
                this.resetSheetSelectionState();
                this.state.selectedRow = null;
            } else if (selection.rowIds.size === 1) {
                this.state.selectedRow = { rowType: selection.rowType, rowId: this.getSelectedRowIds()[0] };
            } else {
                this.state.selectedRow = null;
            }
        }

        handleSheetGridClick(event) {
            if (event.target.closest('[data-select-all-sheet-rows]')) {
                event.preventDefault();
                const selectableCount = this.getSelectableSheetRows().length;
                if (selectableCount && this.getSelectedRowIds().length === selectableCount) {
                    this.clearSelection();
                } else {
                    this.selectAllSheetRows();
                }
                return;
            }
            const row = event.target.closest('tr[data-row-type][data-row-id]');
            if (!row) return;
            const rowType = row.dataset.rowType;
            const rowId = Number(row.dataset.rowId);
            if (!rowType || !rowId) return;

            const sheet = this.getActiveSheet();
            if (!sheet?.editable) {
                this.selectRow(rowType, rowId);
                return;
            }

            const isAdditive = event.metaKey || event.ctrlKey;
            const selector = event.target.closest('[data-sheet-row-selector]');
            const cell = event.target.closest('td[data-edit-field]');
            if (selector) {
                event.preventDefault();
                this.updateSheetSelection(rowType, rowId, {
                    additive: isAdditive || !event.shiftKey,
                    range: event.shiftKey,
                    cellField: null,
                });
                return;
            }

            if (cell) {
                const cellField = cell.dataset.editField || null;
                this.updateSheetSelection(rowType, rowId, {
                    additive: isAdditive,
                    range: event.shiftKey,
                    cellField,
                });
                this.focusSheetCell(rowId, cellField);
                return;
            }

            this.updateSheetSelection(rowType, rowId, {
                additive: isAdditive,
                range: event.shiftKey,
                cellField: null,
            });
        }

        handleSheetGridKeydown(event) {
            const selector = event.target.closest('[data-sheet-row-selector]');
            if (selector && ['Enter', ' '].includes(event.key)) {
                event.preventDefault();
                selector.click();
                return;
            }

            const cell = event.target.closest('td[data-edit-field]');
            if (!cell) return;
            if (event.key === 'Enter') {
                event.preventDefault();
                this.focusRowEditor();
                return;
            }
            if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return;

            const row = cell.closest('tr[data-row-type][data-row-id]');
            const rowCells = Array.from(row?.querySelectorAll('td[data-edit-field]') || []);
            const rows = Array.from(this.elements.sheetGridContainer.querySelectorAll('tr[data-row-type][data-row-id]'));
            const rowIndex = rows.indexOf(row);
            const columnIndex = rowCells.indexOf(cell);
            let targetCell = null;
            if (event.key === 'ArrowLeft') targetCell = rowCells[columnIndex - 1] || null;
            if (event.key === 'ArrowRight') targetCell = rowCells[columnIndex + 1] || null;
            if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
                const nextRowIndex = rowIndex + (event.key === 'ArrowUp' ? -1 : 1);
                targetCell = rows[nextRowIndex]?.querySelector(`td[data-edit-field="${cell.dataset.editField}"]`) || null;
            }
            if (!targetCell) return;

            event.preventDefault();
            const targetRow = targetCell.closest('tr[data-row-type][data-row-id]');
            this.updateSheetSelection(targetRow.dataset.rowType, Number(targetRow.dataset.rowId), {
                additive: false,
                range: event.shiftKey && ['ArrowUp', 'ArrowDown'].includes(event.key),
                cellField: targetCell.dataset.editField,
            });
            this.focusSheetCell(Number(targetRow.dataset.rowId), targetCell.dataset.editField);
        }

        updateSheetSelection(rowType, rowId, options = {}) {
            const { additive = false, range = false, cellField = null } = options;
            const current = this.state.sheetSelection;
            const isCompatible = current.rowType === rowType && current.cellField === cellField;
            let nextIds = isCompatible ? new Set(current.rowIds) : new Set();
            let anchorRowId = isCompatible ? current.anchorRowId : null;

            if (range && isCompatible && anchorRowId) {
                const rows = this.getSelectableSheetRows().filter((row) => row.row_type === rowType);
                const anchorIndex = rows.findIndex((row) => Number(row.row_id) === Number(anchorRowId));
                const targetIndex = rows.findIndex((row) => Number(row.row_id) === Number(rowId));
                if (anchorIndex >= 0 && targetIndex >= 0) {
                    const [start, end] = [anchorIndex, targetIndex].sort((left, right) => left - right);
                    nextIds = new Set(rows.slice(start, end + 1).map((row) => Number(row.row_id)));
                }
            } else if (additive && isCompatible) {
                if (nextIds.has(rowId)) {
                    nextIds.delete(rowId);
                } else {
                    nextIds.add(rowId);
                }
                anchorRowId = rowId;
            } else {
                nextIds = new Set([rowId]);
                anchorRowId = rowId;
            }

            if (!nextIds.size) {
                this.resetSheetSelectionState();
            } else {
                this.state.sheetSelection = { rowType, rowIds: nextIds, cellField, anchorRowId };
            }
            this.state.selectedRow = nextIds.size === 1
                ? { rowType, rowId: Array.from(nextIds)[0] }
                : null;
            this.state.bulkFeedback = null;
            this.state.editorMode = 'update';
            this.setActiveContextPanel('editor');
            this.renderSelectionSurfaces();
        }

        selectAllSheetRows() {
            const rows = this.getSelectableSheetRows();
            if (!rows.length) return;
            const rowType = rows[0].row_type;
            const compatibleRows = rows.filter((row) => row.row_type === rowType);
            this.state.sheetSelection = {
                rowType,
                rowIds: new Set(compatibleRows.map((row) => Number(row.row_id))),
                cellField: null,
                anchorRowId: Number(compatibleRows[0].row_id),
            };
            this.state.selectedRow = compatibleRows.length === 1
                ? { rowType, rowId: Number(compatibleRows[0].row_id) }
                : null;
            this.state.bulkFeedback = null;
            this.state.editorMode = 'update';
            this.setActiveContextPanel('editor');
            this.renderSelectionSurfaces();
        }

        renderSelectionSurfaces() {
            const scrollTop = this.elements.sheetGridContainer.scrollTop;
            const scrollLeft = this.elements.sheetGridContainer.scrollLeft;
            this.renderActiveSheet();
            this.elements.sheetGridContainer.scrollTop = scrollTop;
            this.elements.sheetGridContainer.scrollLeft = scrollLeft;
            this.renderRowEditor();
            this.renderEvidencePreview();
        }

        focusSheetCell(rowId, fieldName) {
            if (!fieldName) return;
            window.setTimeout(() => {
                const cell = this.elements.sheetGridContainer.querySelector(
                    `tr[data-row-id="${rowId}"] td[data-edit-field="${fieldName}"]`,
                );
                cell?.focus({ preventScroll: true });
            }, 0);
        }

        updateSheetSelectionBar(sheet) {
            const selectedIds = this.getSelectedRowIds();
            const isEditableSelection = Boolean(sheet?.editable && selectedIds.length);
            this.elements.sheetSelectionBar.hidden = !isEditableSelection;
            if (!isEditableSelection) return;

            const fieldName = this.state.sheetSelection.cellField;
            const column = (sheet.columns || []).find((candidate) => candidate.edit_field === fieldName);
            const fieldLabel = column?.label || this.bulkFieldLabel(fieldName);
            this.elements.sheetSelectionTitle.textContent = fieldName
                ? `${selectedIds.length} ${fieldLabel} cell${selectedIds.length === 1 ? '' : 's'} selected`
                : `${selectedIds.length} row${selectedIds.length === 1 ? '' : 's'} selected`;
            this.elements.sheetSelectionHint.textContent = fieldName
                ? `Column locked to ${fieldLabel}. Set the new value in Bulk edit.`
                : 'Choose the field and new value in Bulk edit.';
            const selectableCount = this.getSelectableSheetRows().length;
            this.elements.selectAllSheetRowsBtn.textContent = selectedIds.length === selectableCount ? 'All selected' : `Select all ${selectableCount}`;
            this.elements.selectAllSheetRowsBtn.disabled = selectedIds.length === selectableCount;
            if (this.elements.sheetSelectionLive) {
                this.elements.sheetSelectionLive.textContent = this.elements.sheetSelectionTitle.textContent;
            }
        }

        sheetColumnWidth(column) {
            const fieldName = column.edit_field || column.key || '';
            if (fieldName === 'source') return 96;
            if (['reservation_identifier', 'confirmation_code'].includes(fieldName)) return 132;
            if (fieldName === 'guest_name') return 150;
            if (fieldName === 'property_code') return 210;
            if (['start_date', 'end_date', 'service_date', 'transaction_date', 'booking_date'].includes(fieldName)) return 108;
            if (['gross_amount', 'paid_out_amount', 'commission_amount', 'hostaway_fee_amount', 'stripe_fee_amount', 'cleaning_fee_amount', 'tax_amount', 'refund_amount', 'total', 'effective_total'].includes(fieldName)) return 112;
            if (fieldName === 'needs_review') return 92;
            if (fieldName === 'category') return 112;
            if (fieldName === 'item_name') return 190;
            if (fieldName === 'vendor') return 180;
            if (fieldName === 'payment_method') return 140;
            return 140;
        }

        rowSelectionAriaLabel(row, rowIndex) {
            const context = row.guest_name
                || row.item_name
                || row.property_code
                || row.vendor
                || row.reservation_identifier
                || '';
            return `Select row ${rowIndex + 1}${context ? `, ${context}` : ''}`;
        }

        renderActiveSheet() {
            const sheet = this.getActiveSheet();
            if (!sheet) {
                this.elements.sheetGridContainer.innerHTML = '<div class="bk-sheet-empty">No sheet selected.</div>';
                this.elements.sheetSelectionBar.hidden = true;
                return;
            }

            this.reconcileSheetSelection();

            this.elements.sheetStatusBadge.textContent = sheet.editable ? 'Editable' : 'Read only';
            this.elements.sheetMeta.textContent = `${sheet.label} · ${(sheet.rows || []).length} row${(sheet.rows || []).length === 1 ? '' : 's'}${sheet.editable ? ' · select a cell to edit; use the row gutter for bulk actions' : ''}`;
            this.elements.sheetMetaSummary.textContent = `${sheet.label} · ${(sheet.rows || []).length} row${(sheet.rows || []).length === 1 ? '' : 's'}`;
            this.elements.addSheetRowBtn.disabled = !(sheet.key === 'expenses_all' || sheet.key === 'revenue_all');

            if (!(sheet.rows || []).length) {
                this.elements.sheetGridContainer.innerHTML = '<div class="bk-sheet-empty">This sheet does not have any rows yet.</div>';
                this.updateSheetSelectionBar(sheet);
                return;
            }

            const headers = sheet.columns || [];
            const selectedIds = new Set(this.getSelectedRowIds());
            const selectedCellField = this.state.sheetSelection.cellField;
            const showRowSelectors = Boolean(sheet.editable && this.getSelectableSheetRows().length);
            const rowsHtml = (sheet.rows || []).map((row, rowIndex) => {
                const rowId = Number(row.row_id);
                const isSelected = Boolean(row.row_id && selectedIds.has(rowId));
                const selectionClass = isSelected
                    ? (selectedCellField ? 'is-cell-range-selected' : (selectedIds.size > 1 ? 'is-range-selected' : 'is-selected'))
                    : '';
                const rowNeedsReview = row.needs_review || row.kind === 'change_proposal' || (row.reason && sheet.key === 'review_queue');
                return `
                    <tr
                        class="${selectionClass}${rowNeedsReview ? ' needs-review' : ''}"
                        ${row.row_id ? `data-row-id="${row.row_id}" data-row-type="${row.row_type || ''}"` : ''}
                        ${isSelected ? 'aria-selected="true"' : ''}
                        aria-rowindex="${rowIndex + 2}"
                    >
                        ${showRowSelectors ? `
                            <td class="bk-row-selector-cell">
                                ${row.row_id ? `
                                    <label class="bk-row-selector-hit" data-sheet-row-selector tabindex="0" aria-label="${this.escapeHtml(this.rowSelectionAriaLabel(row, rowIndex))}">
                                        <input class="bk-row-selector" type="checkbox" tabindex="-1" ${isSelected ? 'checked' : ''} aria-hidden="true">
                                        <span class="bk-row-number" aria-hidden="true">${rowIndex + 1}</span>
                                    </label>
                                ` : '—'}
                            </td>
                        ` : ''}
                        ${headers.map((column, columnIndex) => {
                            const editField = row.row_id ? (column.edit_field || '') : '';
                            const isCellSelected = Boolean(isSelected && selectedCellField && editField === selectedCellField);
                            const isActiveCell = Boolean(isCellSelected && Number(this.state.sheetSelection.anchorRowId) === rowId);
                            const previousRowId = Number((sheet.rows || [])[rowIndex - 1]?.row_id);
                            const nextRowId = Number((sheet.rows || [])[rowIndex + 1]?.row_id);
                            const isCellRangeStart = Boolean(isCellSelected && !selectedIds.has(previousRowId));
                            const isCellRangeEnd = Boolean(isCellSelected && !selectedIds.has(nextRowId));
                            const cellSelectionClasses = [
                                isCellSelected ? 'is-cell-selected' : '',
                                isCellRangeStart ? 'is-cell-range-start' : '',
                                isCellRangeEnd ? 'is-cell-range-end' : '',
                            ].filter(Boolean).join(' ');
                            return `<td
                                data-column-key="${this.escapeHtml(column.key || '')}"
                                aria-colindex="${columnIndex + (showRowSelectors ? 2 : 1)}"
                                ${editField ? `data-edit-field="${this.escapeHtml(editField)}" tabindex="${isActiveCell || (!selectedCellField && rowIndex === 0 && columnIndex === 0) ? '0' : '-1'}" aria-label="${this.escapeHtml(`${column.label}: ${this.formatCellText(row[column.key])}`)}"` : ''}
                                ${isCellSelected ? 'aria-selected="true"' : ''}
                                class="${cellSelectionClasses}"
                            >${this.formatCell(row[column.key])}</td>`;
                        }).join('')}
                    </tr>
                `;
            }).join('');

            this.elements.sheetGridContainer.innerHTML = `
                <table class="bk-sheet-table" role="grid" aria-multiselectable="true" aria-label="${this.escapeHtml(sheet.label)} spreadsheet">
                    <colgroup>
                        ${showRowSelectors ? '<col style="width:38px">' : ''}
                        ${headers.map((column) => `<col style="width:${this.sheetColumnWidth(column)}px">`).join('')}
                    </colgroup>
                    <thead>
                        <tr aria-rowindex="1">
                            ${showRowSelectors ? '<th class="bk-row-selector-cell"><label class="bk-row-selector-hit is-master" data-select-all-sheet-rows tabindex="0" aria-label="Select all rows"><input class="bk-row-selector" type="checkbox" tabindex="-1" aria-hidden="true"></label></th>' : ''}
                            ${headers.map((column, columnIndex) => `<th data-column-key="${this.escapeHtml(column.key || '')}" aria-colindex="${columnIndex + (showRowSelectors ? 2 : 1)}">${this.escapeHtml(column.label)}</th>`).join('')}
                        </tr>
                    </thead>
                    <tbody>${rowsHtml}</tbody>
                </table>
            `;
            const selectAllCheckbox = this.elements.sheetGridContainer.querySelector('[data-select-all-sheet-rows] .bk-row-selector');
            if (selectAllCheckbox) {
                const selectableCount = this.getSelectableSheetRows().length;
                selectAllCheckbox.checked = Boolean(selectableCount && selectedIds.size === selectableCount);
                selectAllCheckbox.indeterminate = Boolean(selectedIds.size && selectedIds.size < selectableCount);
                const selectAllHitTarget = selectAllCheckbox.closest('[data-select-all-sheet-rows]');
                if (selectAllHitTarget) {
                    selectAllHitTarget.setAttribute('aria-label', selectAllCheckbox.checked ? 'Clear all selected rows' : 'Select all rows');
                }
            }
            this.updateSheetSelectionBar(sheet);
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
            const selectedIds = this.getSelectedRowIds();
            if (selectedIds.length > 1) {
                this.renderBulkEditor();
                return;
            }
            if (this.elements.rowEditorTitle) this.elements.rowEditorTitle.textContent = 'Row editor';
            if (!selection) {
                this.elements.selectionBadge.textContent = this.state.bulkFeedback ? 'Bulk update complete' : 'No row selected';
                this.elements.rowEditor.innerHTML = this.state.bulkFeedback
                    ? `<div class="bk-bulk-editor-intro"><strong>${this.escapeHtml(this.state.bulkFeedback)}</strong><br>The live workbook has been refreshed with the saved values.</div>`
                    : '<div class="bk-empty">Select a row to edit it, or use the row checkboxes and Shift / Command / Ctrl to build a bulk selection.</div>';
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

        bulkFieldDefinitions(rowType) {
            const shared = {
                property_code: { label: 'Property', type: 'text', placeholder: 'Property or listing code' },
                needs_review: { label: 'Review', type: 'boolean' },
            };
            if (rowType === 'expense_item') {
                return {
                    service_date: { label: 'Service date', type: 'date' },
                    category: { label: 'Category', type: 'category' },
                    item_name: { label: 'Item', type: 'text', placeholder: 'Expense item' },
                    vendor: { label: 'Vendor', type: 'text', placeholder: 'Vendor name' },
                    property_code: shared.property_code,
                    total: { label: 'Amount', type: 'number', step: '0.01' },
                    payment_method: { label: 'Payment method', type: 'text', placeholder: 'Zelle, card, cash…' },
                    needs_review: shared.needs_review,
                };
            }
            if (rowType === 'revenue_item') {
                return {
                    reservation_identifier: { label: 'Reservation ID', type: 'text' },
                    confirmation_code: { label: 'Confirmation code', type: 'text' },
                    guest_name: { label: 'Guest', type: 'text' },
                    property_code: shared.property_code,
                    transaction_date: { label: 'Transaction date', type: 'date' },
                    booking_date: { label: 'Booking date', type: 'date' },
                    start_date: { label: 'Start date', type: 'date' },
                    end_date: { label: 'End date', type: 'date' },
                    nights: { label: 'Nights', type: 'number', step: '1' },
                    gross_amount: { label: 'Gross amount', type: 'number', step: '0.01' },
                    paid_out_amount: { label: 'Paid out', type: 'number', step: '0.01' },
                    commission_amount: { label: 'Commission', type: 'number', step: '0.01' },
                    hostaway_fee_amount: { label: 'Hostaway fee', type: 'number', step: '0.01' },
                    stripe_fee_amount: { label: 'Stripe fee', type: 'number', step: '0.01' },
                    cleaning_fee_amount: { label: 'Cleaning fee', type: 'number', step: '0.01' },
                    tax_amount: { label: 'Tax', type: 'number', step: '0.01' },
                    refund_amount: { label: 'Refund', type: 'number', step: '0.01' },
                    details: { label: 'Details', type: 'text' },
                    needs_review: shared.needs_review,
                };
            }
            return {};
        }

        bulkFieldLabel(fieldName) {
            if (!fieldName) return 'selected';
            const rowType = this.state.sheetSelection?.rowType;
            return this.bulkFieldDefinitions(rowType)[fieldName]?.label
                || fieldName.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
        }

        renderBulkEditor() {
            const selection = this.state.sheetSelection;
            const selectedIds = this.getSelectedRowIds();
            const definitions = this.bulkFieldDefinitions(selection.rowType);
            const lockedField = selection.cellField;
            const fieldOptions = Object.entries(definitions).map(([fieldName, definition]) => `
                <option value="${fieldName}">${this.escapeHtml(definition.label)}</option>
            `).join('');
            const modeLabel = lockedField ? `${selectedIds.length} cells` : `${selectedIds.length} rows`;
            if (this.elements.rowEditorTitle) this.elements.rowEditorTitle.textContent = 'Bulk edit';
            this.elements.selectionBadge.textContent = `${modeLabel} selected`;
            this.elements.rowEditor.innerHTML = `
                <form id="bulkEditorForm" data-row-type="${this.escapeHtml(selection.rowType)}" class="bk-inline-editor">
                    <div class="bk-bulk-editor-intro">
                        <strong>Update ${modeLabel} together.</strong><br>
                        The change is saved as one atomic operation. If any selected record changed since this sheet loaded, nothing will be updated.
                    </div>
                    <label>Column
                        ${lockedField ? `
                            <input type="hidden" name="bulk_field" value="${this.escapeHtml(lockedField)}">
                            <div class="bk-bulk-field-lock">${this.escapeHtml(this.bulkFieldLabel(lockedField))}<span>Locked to selected cells</span></div>
                        ` : `
                            <select name="bulk_field" required>
                                <option value="">Choose a column…</option>
                                ${fieldOptions}
                            </select>
                        `}
                    </label>
                    <div class="bk-bulk-value-control">
                        ${lockedField ? this.buildBulkValueControl(lockedField, selection.rowType) : '<div class="bk-empty">Choose the column whose value you want to replace.</div>'}
                    </div>
                    <label>Reason for this bulk change
                        <textarea name="edit_note" required placeholder="Explain why these selected records need the same value."></textarea>
                    </label>
                    <div class="bk-inline-actions">
                        <button class="btn btn-primary" type="submit">Update ${selectedIds.length} ${lockedField ? 'cells' : 'rows'}</button>
                        <button class="btn btn-secondary" type="button" data-clear-sheet-selection>Clear selection</button>
                    </div>
                    <div data-bulk-status aria-live="polite"></div>
                </form>
            `;
        }

        buildBulkValueControl(fieldName, rowType) {
            const definition = this.bulkFieldDefinitions(rowType)[fieldName];
            if (!definition) {
                return '<div class="bk-empty">This column cannot be updated in bulk.</div>';
            }
            if (definition.type === 'category') {
                const options = (this.state.referenceData?.expense_categories || []).map((entry) => `
                    <option value="${this.escapeHtml(entry.value)}">${this.escapeHtml(entry.label)}</option>
                `).join('');
                return `<label>New ${this.escapeHtml(definition.label)}<select name="value" required><option value="">Choose a category…</option>${options}</select></label>`;
            }
            if (definition.type === 'boolean') {
                return `<label>New ${this.escapeHtml(definition.label)}<select name="value" required><option value="false">No</option><option value="true">Yes</option></select></label>`;
            }
            return `
                <label>New ${this.escapeHtml(definition.label)}
                    <input name="value" type="${definition.type}" ${definition.step ? `step="${definition.step}"` : ''} placeholder="${this.escapeHtml(definition.placeholder || '')}" required>
                </label>
            `;
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
            const selectedCount = this.getSelectedRowIds().length;
            if (selectedCount > 1) {
                this.elements.evidencePreview.innerHTML = `<div class="bk-empty">${selectedCount} records are selected. Clear the bulk selection or choose one row to inspect its source evidence.</div>`;
                return;
            }
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
            this.resetSheetSelectionState();
            this.state.bulkFeedback = null;
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
            this.resetSheetSelectionState();
            this.state.bulkFeedback = null;
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
            this.resetSheetSelectionState();
            this.state.bulkFeedback = null;
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
            this.resetSheetSelectionState();
            this.state.bulkFeedback = null;
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
            this.state.sheetSelection = {
                rowType,
                rowIds: rowId ? new Set([Number(rowId)]) : new Set(),
                cellField: null,
                anchorRowId: rowId ? Number(rowId) : null,
            };
            this.state.bulkFeedback = null;
            this.state.editorMode = 'update';
            this.setActiveContextPanel('editor');
            this.renderActiveSheet();
            this.renderRowEditor();
            this.renderEvidencePreview();
        }

        clearSelection() {
            this.state.selectedRow = null;
            this.resetSheetSelectionState();
            this.state.bulkFeedback = null;
            this.state.editorMode = 'update';
            this.renderActiveSheet();
            this.renderRowEditor();
            this.renderEvidencePreview();
        }

        async handleRowEditorSubmit(event) {
            event.preventDefault();
            const bulkForm = event.target.closest('#bulkEditorForm');
            if (bulkForm) {
                await this.handleBulkEditorSubmit(bulkForm);
                return;
            }
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

        async handleBulkEditorSubmit(form) {
            if (!this.state.selectedPeriodId) return;
            const selection = this.state.sheetSelection;
            const rowIds = this.getSelectedRowIds();
            if (rowIds.length < 2) {
                this.clearSelection();
                return;
            }

            const formData = new FormData(form);
            const fieldName = String(formData.get('bulk_field') || '');
            const definition = this.bulkFieldDefinitions(selection.rowType)[fieldName];
            const status = form.querySelector('[data-bulk-status]');
            const submitButton = form.querySelector('[type="submit"]');
            if (!definition) {
                if (status) status.innerHTML = '<div class="bk-empty">Choose a column to update.</div>';
                return;
            }

            let value = formData.get('value');
            if (definition.type === 'boolean') {
                value = value === 'true';
            }
            const updatedAtById = {};
            rowIds.forEach((rowId) => {
                const item = selection.rowType === 'expense_item'
                    ? this.findExpenseItem(rowId)
                    : this.findRevenueItem(rowId);
                updatedAtById[String(rowId)] = item?.updated_at || '';
            });

            submitButton.disabled = true;
            submitButton.textContent = `Updating ${rowIds.length} records…`;
            if (status) status.textContent = '';
            try {
                const data = await this.fetchJson(`/bookkeeping/api/periods/${this.state.selectedPeriodId}/items/bulk-update`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        row_type: selection.rowType,
                        row_ids: rowIds,
                        field: fieldName,
                        value,
                        edit_note: String(formData.get('edit_note') || ''),
                        updated_at_by_id: updatedAtById,
                    }),
                });
                const changedCount = Number(data.updated_count || 0);
                this.state.bulkFeedback = changedCount
                    ? `${changedCount} record${changedCount === 1 ? '' : 's'} updated successfully.`
                    : 'The selected records already had that value; no changes were needed.';
                this.resetSheetSelectionState();
                this.state.selectedRow = null;
                await this.refreshWorkspace();
            } catch (error) {
                submitButton.disabled = false;
                submitButton.textContent = `Update ${rowIds.length} ${selection.cellField ? 'cells' : 'rows'}`;
                if (status) {
                    status.innerHTML = `<div class="bk-empty">${this.escapeHtml(error.message || 'The bulk update could not be saved.')}</div>`;
                }
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
            if (event.target.name === 'bulk_field') {
                const form = event.target.closest('#bulkEditorForm');
                const container = form?.querySelector('.bk-bulk-value-control');
                if (container) {
                    container.innerHTML = event.target.value
                        ? this.buildBulkValueControl(event.target.value, form.dataset.rowType)
                        : '<div class="bk-empty">Choose the column whose value you want to replace.</div>';
                }
                return;
            }
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

        handleExpenseFileSelection() {
            const files = Array.from(this.elements.expenseFilesInput.files || []);
            this.elements.expenseFilePrompt.textContent = files.length > 1
                ? `${files.length} receipts selected`
                : (files[0]?.name || 'Select clear images or PDFs');
        }

        resetExpenseUploadForm() {
            this.elements.expenseUploadForm.reset();
            this.elements.expenseFilePrompt.textContent = 'Select clear images or PDFs';
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
                window.alert(stage === 'expense' ? 'Choose one or more receipts to analyze.' : 'Choose at least one file to upload.');
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
                    title: stage === 'expense' ? 'Uploading receipt batch' : 'Uploading files',
                    meta: stage === 'expense'
                        ? `Sending ${files.length} receipt${files.length === 1 ? '' : 's'} to Cotton Candy for extraction and naming.`
                        : 'Sending files to Cotton Candy and waiting for bookkeeping normalization.',
                    progress: 3,
                });

                xhr.upload.onprogress = (event) => {
                    if (!event.lengthComputable) return;
                    const percent = Math.max(3, Math.round((event.loaded / event.total) * 92));
                    this.setUploadStatus(statusElement, {
                        title: stage === 'expense' ? 'Uploading receipt batch' : 'Uploading files',
                        meta: `${files.length} file${files.length === 1 ? '' : 's'} in flight`,
                        progress: percent,
                    });
                };

                xhr.upload.onload = () => {
                    this.setUploadStatus(statusElement, {
                        title: stage === 'expense' ? 'Reading receipts' : 'Processing uploaded files',
                        meta: stage === 'expense'
                            ? 'Cotton Candy is extracting each receipt and preparing the review queue.'
                            : 'Cotton Candy received the files and is extracting bookkeeping rows on the server.',
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
                            if (stage === 'expense') {
                                this.setPendingReceiptUploads(responseData.processing_batch.uploads);
                            } else if (stage !== 'expense') {
                                form.reset();
                            }
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
                            if (stage === 'expense') {
                                this.setPendingReceiptUploads(responseData.uploads);
                            } else {
                                form.reset();
                            }
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
                        if (stage === 'expense') {
                            this.setPendingReceiptUploads(responseData.uploads);
                            this.openPendingReceiptAfterProcessing();
                        } else {
                            form.reset();
                        }
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
            const reviewReceiptButton = event.target.closest('[data-review-receipt-id]');
            if (reviewReceiptButton) {
                this.openReceiptOrganizer(Number(reviewReceiptButton.dataset.reviewReceiptId));
                return;
            }
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

        openPendingReceiptAfterProcessing() {
            const uploadsById = new Map(
                (this.state.workspace?.uploads || []).map((upload) => [upload.bookkeeping_upload_id, upload])
            );
            this.state.pendingReceiptUploadIds = this.state.pendingReceiptUploadIds.filter((uploadId) => {
                const upload = uploadsById.get(uploadId);
                return upload && upload.upload_status !== 'failed';
            });
            this.state.receiptReviewTotal = this.state.pendingReceiptUploadIds.length;
            if (!this.state.pendingReceiptUploadIds.length) {
                this.closeReceiptOrganizer();
                return;
            }
            const nextUpload = uploadsById.get(this.state.pendingReceiptUploadIds[0]);
            if (!nextUpload || ['queued', 'processing'].includes(nextUpload.upload_status)) return;
            this.openReceiptOrganizer(nextUpload.bookkeeping_upload_id, { preserveQueue: true });
        }

        setReceiptReviewQueueFrom(uploadId) {
            const reviewableUploadIds = (this.state.workspace?.uploads || [])
                .filter((entry) => (
                    entry.stage === 'expense'
                    && !['queued', 'processing', 'failed'].includes(entry.upload_status)
                ))
                .map((entry) => entry.bookkeeping_upload_id);
            const selectedIndex = reviewableUploadIds.indexOf(uploadId);
            if (selectedIndex < 0) {
                this.state.pendingReceiptUploadIds = [uploadId];
                this.state.receiptReviewTotal = 1;
                this.state.receiptReviewCompleted = 0;
                return;
            }
            this.state.pendingReceiptUploadIds = reviewableUploadIds.slice(selectedIndex);
            this.state.receiptReviewTotal = reviewableUploadIds.length;
            this.state.receiptReviewCompleted = selectedIndex;
        }

        openReceiptOrganizer(uploadId, options = {}) {
            const upload = (this.state.workspace?.uploads || []).find((entry) => entry.bookkeeping_upload_id === uploadId);
            if (!upload) return;
            const organization = this.receiptOrganizationForUpload(upload);
            const previewUrl = `/bookkeeping/api/uploads/${uploadId}/file`;
            const previewKind = this.resolveUploadPreviewKind(upload);

            if (!options.preserveQueue) {
                this.setReceiptReviewQueueFrom(uploadId);
            }

            this.state.activeReceiptUploadId = uploadId;
            this.state.isReceiptEditorOpen = true;
            this.elements.receiptOrganizerOriginalName.textContent = upload.original_filename || 'Untitled upload';
            if (previewKind === 'image') {
                this.elements.receiptOrganizerPreview.innerHTML = `<img src="${previewUrl}" alt="${this.escapeHtml(upload.original_filename || 'Receipt preview')}">`;
            } else if (previewKind === 'pdf') {
                this.elements.receiptOrganizerPreview.innerHTML = `<iframe src="${previewUrl}" title="${this.escapeHtml(upload.original_filename || 'Receipt preview')}"></iframe>`;
            } else {
                this.elements.receiptOrganizerPreview.innerHTML = `<a class="btn btn-secondary" href="${previewUrl}" target="_blank" rel="noopener noreferrer">Open source file</a>`;
            }

            this.elements.receiptOrganizerDate.value = organization.receipt_date || '';
            this.elements.receiptOrganizerType.value = organization.document_group || 'operations';
            this.elements.receiptOrganizerExpenseType.value = organization.expense_type || '';
            this.elements.receiptOrganizerStore.value = organization.store_name || '';
            this.state.generatedReceiptFilename = organization.suggested_filename || null;
            this.elements.receiptOrganizerFilename.value = organization.effective_filename || organization.suggested_filename || '';
            this.elements.receiptOrganizerReviewNote.textContent = !organization.status
                ? 'This receipt predates AI naming. Add the date and receipt context to create its filing name.'
                : (organization.missing_fields || []).length
                ? `Cotton Candy needs confirmation for: ${organization.missing_fields.join(', ')}.`
                : (organization.status === 'approved'
                    ? 'This name is approved. Editing and approving again will update the future Drive filing target.'
                    : 'The AI suggestion is ready. Confirm it once and the file will use this name during Drive sync.');
            this.elements.receiptOrganizerWorkspace.hidden = false;
            this.elements.stepModalFooter.hidden = true;
            this.updateReceiptOrganizerPreview();
            this.updateReceiptReviewProgress();
            this.renderStageUploadControls(this.state.workspace?.uploads || []);
            window.setTimeout(() => {
                this.elements.receiptOrganizerWorkspace.scrollIntoView({ behavior: 'smooth', block: 'start' });
                this.elements.receiptOrganizerDate.focus({ preventScroll: true });
            }, 0);
        }

        closeReceiptOrganizer() {
            this.state.activeReceiptUploadId = null;
            this.state.pendingReceiptUploadIds = [];
            this.state.receiptReviewTotal = 0;
            this.state.receiptReviewCompleted = 0;
            this.state.generatedReceiptFilename = null;
            this.state.isReceiptEditorOpen = false;
            this.elements.receiptOrganizerWorkspace.hidden = true;
            this.elements.stepModalFooter.hidden = false;
            this.resetExpenseUploadForm();
            this.renderStageUploadControls(this.state.workspace?.uploads || []);
        }

        updateReceiptReviewProgress() {
            const total = Math.max(this.state.receiptReviewTotal, this.state.pendingReceiptUploadIds.length, 1);
            const position = Math.min(this.state.receiptReviewCompleted + 1, total);
            this.elements.receiptOrganizerProgress.textContent = `2 · Receipt ${position} of ${total}`;
            this.elements.receiptOrganizerCloseBtn.textContent = total > 1 ? 'Exit batch review' : 'Back to upload';
            this.elements.receiptOrganizerSaveBtn.textContent = 'Save';
            this.elements.receiptOrganizerNextBtn.textContent = 'Next';
        }

        advanceReceiptReview() {
            const completedUploadId = this.state.activeReceiptUploadId;
            const total = Math.max(this.state.receiptReviewTotal, 1);
            this.state.pendingReceiptUploadIds = this.state.pendingReceiptUploadIds
                .filter((uploadId) => uploadId !== completedUploadId);
            this.state.receiptReviewCompleted = Math.min(this.state.receiptReviewCompleted + 1, total);
            const progress = {
                completed: this.state.receiptReviewCompleted,
                total,
                remaining: this.state.pendingReceiptUploadIds.length,
            };
            if (progress.remaining) {
                this.openReceiptOrganizer(this.state.pendingReceiptUploadIds[0], { preserveQueue: true });
            } else {
                this.closeReceiptOrganizer();
            }
            return progress;
        }

        receiptDateLabel(value) {
            const parts = String(value || '').split('-').map(Number);
            if (parts.length !== 3 || parts.some((part) => !part)) return 'Date needed';
            const [year, month, day] = parts;
            const monthName = new Intl.DateTimeFormat('en-US', { month: 'long', timeZone: 'UTC' })
                .format(new Date(Date.UTC(year, month - 1, 1)));
            return `${monthName} ${day}, ${year}`;
        }

        receiptPeriodPath() {
            const filingDate = this.elements.receiptOrganizerDate?.value
                || this.state.workspace?.period?.period_start
                || '';
            const parts = String(filingDate).split('-').map(Number);
            if (parts.length !== 3 || !parts[0] || !parts[1]) {
                return { year: 'Year needed', month: 'Month needed' };
            }
            const [year, month] = parts;
            const monthName = new Intl.DateTimeFormat('en-US', { month: 'long', timeZone: 'UTC' })
                .format(new Date(Date.UTC(year, month - 1, 1)));
            return { year: String(year), month: `${month}. ${monthName} ${year}` };
        }

        receiptOriginalExtension() {
            const upload = (this.state.workspace?.uploads || []).find((entry) => entry.bookkeeping_upload_id === this.state.activeReceiptUploadId);
            const filename = String(upload?.original_filename || 'receipt.jpg');
            const dotIndex = filename.lastIndexOf('.');
            return dotIndex >= 0 ? filename.slice(dotIndex).toLowerCase() : '.jpg';
        }

        generatedReceiptName() {
            const type = this.elements.receiptOrganizerType.value;
            const dateLabel = this.receiptDateLabel(this.elements.receiptOrganizerDate.value);
            const extension = this.receiptOriginalExtension();
            if (type === 'operations') {
                const expenseType = this.elements.receiptOrganizerExpenseType.value.trim() || 'Expense type needed';
                return `${dateLabel} - ${expenseType}${extension}`;
            }
            const receiptKind = type === 'reimbursement' ? 'Reimbursement Receipt' : 'Purchase Receipt';
            const storeName = this.elements.receiptOrganizerStore.value.trim() || 'Store needed';
            return `${dateLabel} - ${receiptKind} - ${storeName}${extension}`;
        }

        updateReceiptOrganizerPreview() {
            const type = this.elements.receiptOrganizerType.value;
            const isOperations = type === 'operations';
            this.elements.receiptOrganizerExpenseTypeField.hidden = !isOperations;
            this.elements.receiptOrganizerStoreField.hidden = isOperations;
            this.elements.receiptOrganizerExpenseType.required = isOperations;
            this.elements.receiptOrganizerStore.required = !isOperations;

            const generatedFilename = this.generatedReceiptName();
            const currentFilename = this.elements.receiptOrganizerFilename.value.trim();
            if (!currentFilename || currentFilename === this.state.generatedReceiptFilename) {
                this.elements.receiptOrganizerFilename.value = generatedFilename;
            }
            this.state.generatedReceiptFilename = generatedFilename;
            this.updateReceiptOrganizerDestination();
        }

        updateReceiptOrganizerDestination() {
            const type = this.elements.receiptOrganizerType.value;
            const folder = type === 'operations'
                ? 'Cleanings, Maintenance & Misc. Receipts'
                : 'Purchase & Reimbursement Receipts';
            const periodPath = this.receiptPeriodPath();
            const filename = this.elements.receiptOrganizerFilename.value.trim() || this.generatedReceiptName();
            this.elements.receiptOrganizerFilenamePreview.textContent = filename;
            this.elements.receiptOrganizerPathPreview.textContent = `${periodPath.year} / ${periodPath.month} / ${folder}`;
        }

        async nextReceiptOrganization() {
            if (!this.state.activeReceiptUploadId) return;
            if (this.elements.receiptOrganizerForm.checkValidity()) {
                await this.saveReceiptOrganization({ advance: true });
                return;
            }
            const reviewProgress = this.advanceReceiptReview();
            this.setUploadStatus(this.elements.expenseUploadStatus, {
                title: reviewProgress.remaining
                    ? 'Skipped · next receipt ready'
                    : 'Receipt review complete',
                meta: reviewProgress.remaining
                    ? `${reviewProgress.completed} of ${reviewProgress.total} visited. The skipped receipt was left unchanged.`
                    : `Reached the end of ${reviewProgress.total} receipt${reviewProgress.total === 1 ? '' : 's'}. Incomplete receipts were left unchanged.`,
                progress: Math.round((reviewProgress.completed / reviewProgress.total) * 100),
            });
        }

        async saveReceiptOrganization({ advance = false } = {}) {
            if (!this.state.activeReceiptUploadId || !this.elements.receiptOrganizerForm.reportValidity()) {
                return;
            }
            const buttons = [this.elements.receiptOrganizerSaveBtn, this.elements.receiptOrganizerNextBtn];
            buttons.forEach((button) => { button.disabled = true; });
            try {
                await this.fetchJson(`/bookkeeping/api/uploads/${this.state.activeReceiptUploadId}/receipt-organization`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        receipt_date: this.elements.receiptOrganizerDate.value,
                        document_group: this.elements.receiptOrganizerType.value,
                        expense_type: this.elements.receiptOrganizerExpenseType.value.trim(),
                        store_name: this.elements.receiptOrganizerStore.value.trim(),
                        filename: this.elements.receiptOrganizerFilename.value.trim(),
                        status: 'approved',
                    }),
                });
                await this.refreshWorkspace();
                if (!advance) {
                    const total = Math.max(this.state.receiptReviewTotal, 1);
                    const position = Math.min(this.state.receiptReviewCompleted + 1, total);
                    this.elements.receiptOrganizerReviewNote.textContent = 'Saved. This receipt will use the confirmed name during Drive sync.';
                    this.setUploadStatus(this.elements.expenseUploadStatus, {
                        title: 'Receipt saved',
                        meta: `Still reviewing receipt ${position} of ${total}. Select Next when you are ready to continue.`,
                        progress: Math.round((position / total) * 100),
                    });
                    return;
                }
                const reviewProgress = this.advanceReceiptReview();
                this.setUploadStatus(this.elements.expenseUploadStatus, {
                    title: reviewProgress.remaining
                        ? 'Saved · next receipt ready'
                        : 'Receipt batch review complete',
                    meta: reviewProgress.remaining
                        ? `${reviewProgress.completed} of ${reviewProgress.total} reviewed. Confirm the next AI-suggested name.`
                        : `${reviewProgress.completed} receipt${reviewProgress.completed === 1 ? '' : 's'} reviewed. You can upload another batch or reopen any receipt below.`,
                    progress: Math.round((reviewProgress.completed / reviewProgress.total) * 100),
                });
            } catch (error) {
                window.alert(error.message || 'Could not save the receipt name.');
            } finally {
                buttons.forEach((button) => { button.disabled = false; });
            }
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
            const didChangeSheet = this.state.activeSheetKey !== sheetKey;
            this.state.activeSheetKey = sheetKey;
            if (didChangeSheet) {
                this.state.selectedRow = null;
                this.resetSheetSelectionState();
                this.state.bulkFeedback = null;
                this.renderRowEditor();
                this.renderEvidencePreview();
            }
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
            return this.escapeHtml(this.formatCellText(value)).replace(/\n/g, ' ');
        }

        formatCellText(value) {
            if (value === null || value === undefined || value === '') return '—';
            if (typeof value === 'boolean') return value ? 'Yes' : 'No';
            if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2);
            return String(value);
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
