<script lang="ts">
	import { toast } from 'svelte-sonner';

	import { onMount, getContext, onDestroy, tick } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18nType } from 'i18next';

	const i18n = getContext<Writable<I18nType>>('i18n');
	import dayjs from '$lib/dayjs';

	import { browser } from '$app/environment';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { config, user, settings } from '$lib/stores';

	import { uploadFile, checkinFileById, checkoutFileById, getFileById } from '$lib/apis/files';
	import {
		addFileToKnowledgeById,
		addNoteToKnowledgeById,
		getKnowledgeSynthesisById,
		getKnowledgeSynthesisStatusById,
		getKnowledgeById,
		regenerateKnowledgeSynthesisById,
		removeFileFromKnowledgeById,
		removeNoteFromKnowledgeById,
		startKnowledgeSynthesisById,
		upsertDirectorySyncFileToKnowledge,
		updateKnowledgeById,
		updateKnowledgeAccessGrants
	} from '$lib/apis/knowledge';
	import { searchNotes } from '$lib/apis/notes';
	import {
		createEditableDocumentCopyById,
		suggestDocumentMetadataById,
		updateDocumentMetadataById
	} from '$lib/apis/documents';
	import { processWeb } from '$lib/apis/retrieval';

	import { blobToFile } from '$lib/utils';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Files from './KnowledgeBase/Files.svelte';

	import AddContentMenu from './KnowledgeBase/AddContentMenu.svelte';
	import AddTextContentModal from './KnowledgeBase/AddTextContentModal.svelte';

	import SyncConfirmDialog from '../../common/ConfirmDialog.svelte';
	import Drawer from '$lib/components/common/Drawer.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import LockClosed from '$lib/components/icons/LockClosed.svelte';
	import AccessControlModal from '../common/AccessControlModal.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import FilesOverlay from '$lib/components/chat/MessageInput/FilesOverlay.svelte';
	import DropdownOptions from '$lib/components/common/DropdownOptions.svelte';
	import AttachWebpageModal from '$lib/components/chat/MessageInput/AttachWebpageModal.svelte';
	import Folder from '$lib/components/icons/Folder.svelte';
	import NewFolderAlt from '$lib/components/icons/NewFolderAlt.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import OriginalDocumentPreview from './KnowledgeBase/OriginalDocumentPreview.svelte';
	import DocumentVersions from './KnowledgeBase/DocumentVersions.svelte';

	let showAddWebpageModal = false;
	let showAddTextContentModal = false;

	let showSyncConfirmModal = false;
	let showAccessControlModal = false;
	let showLinkedNotesModal = false;
	let linkedNotesLoading = false;
	let linkedNoteQuery = '';
	type Actor = {
		id?: string | null;
		name?: string | null;
		email?: string | null;
		[key: string]: any;
	};

	type VersionControlState = {
		status?: string;
		revision?: number;
		checked_out_by?: Actor | null;
		checked_out_at?: number | null;
		history?: Record<string, any>[];
		[key: string]: any;
	};

	type DocumentFile = {
		id?: string | null;
		itemId?: string | null;
		tempId?: string | null;
		filename?: string | null;
		name?: string | null;
		file?: File | string | null;
		url?: string | null;
		size?: number | null;
		status?: string | null;
		error?: string | null;
		type?: string | null;
		meta?: Record<string, any>;
		data?: Record<string, any>;
		user?: Actor | null;
		user_id?: string | null;
		created_at?: number | null;
		updated_at?: number | null;
		[key: string]: any;
	};

	type LinkedNote = {
		id: string;
		title?: string | null;
		[key: string]: any;
	};

	type AccessGrant = {
		id?: string;
		principal_type: 'user' | 'group';
		principal_id: string;
		permission: 'read' | 'write';
		[key: string]: any;
	};

	type SynthesisDocumentStatus = {
		documentId?: string | null;
		title?: string | null;
		status?: string | null;
		pageCount?: number | null;
		updatedAt?: number | null;
		sourceType?: string | null;
		warnings?: string[];
		error?: string | null;
	};

	type SynthesisReportPayload = {
		jobId?: string | null;
		collectionId?: string | null;
		status?: string | null;
		documentsTotal?: number;
		documentsCompleted?: number;
		documentsFailed?: number;
		currentStep?: string | null;
		currentDocumentId?: string | null;
		currentDocumentTitle?: string | null;
		warnings?: string[];
		error?: string | null;
		noteId?: string | null;
		noteTitle?: string | null;
		documentStatuses?: SynthesisDocumentStatus[];
		report?: {
			overview?: string;
			recurringThemes?: string[];
			mainFindings?: string[];
			convergences?: string[];
			divergences?: string[];
			gaps?: string[];
			conclusion?: string;
			futureQuestions?: string[];
		} | null;
		metadata?: {
			model?: string | null;
			generatedAt?: number | null;
			chunkConfig?: {
				mode?: string;
				size?: number;
				fallback_mode?: string;
				fallback_size?: number;
			} | null;
			documentsProcessed?: number;
			documentsFailed?: number;
			includedDocumentIds?: string[];
			failedDocuments?: Array<Record<string, any>>;
		} | null;
	};

	let linkedNoteCandidates: LinkedNote[] = [];
	let linkedNoteSearchDebounceTimer: ReturnType<typeof setTimeout> | null = null;
	let showDocumentMetadataModal = false;
	let documentMetadataLoading = false;
	let documentMetadataSaving = false;
	let documentMetadataInstruction = '';
	let documentMetadataDraft = {
		title: '',
		description: '',
		summary: '',
		author: '',
		source: '',
		language: '',
		document_type: '',
		document_status: 'Em elaboração',
		version: '1',
		tags: '',
		entities: '',
		suggested_collection_hints: '',
		folder_path: ''
	};
	let showCollectionMetadataModal = false;
	let collectionMetadataLoading = false;
	let collectionMetadataSaving = false;
	let collectionMetadataInstruction = '';
	let showMoveDocumentModal = false;
	let collectionMetadataDraft = {
		description: '',
		summary: '',
		author: '',
		source: '',
		language: '',
		document_type: '',
		document_status: 'Em elaboração',
		version: '',
		tags: '',
		entities: '',
		folder_path: ''
	};

	type Knowledge = {
		id: string;
		name: string;
		description: string;
		meta?: Record<string, any>;
		files: DocumentFile[];
		linked_notes?: LinkedNote[];
		access_grants?: AccessGrant[];
		write_access?: boolean;
		file_count?: number;
		folder_count?: number;
		folders?: string[];
	};

	const createTempId = () =>
		globalThis.crypto?.randomUUID?.() ??
		`temp-${Date.now()}-${Math.random().toString(36).slice(2)}`;

	let id: string | null = null;
	let knowledge: Knowledge | null = null;
	let knowledgeId: string | null = null;
	let synthesisReport: SynthesisReportPayload | null = null;
	let synthesisLoading = false;
	let synthesisActionLoading = false;
	let synthesisCollapsed = true;
	let synthesisPollTimer: ReturnType<typeof setTimeout> | null = null;

	let selectedFileId: string | null = null;
	let selectedFile: DocumentFile | null = null;
	let selectedFileContent = '';
	let canEditSelectedFileContent = false;
	let selectedFileCheckoutOwnerLabel = '';
	let selectedFileTab: 'markdown' | 'original' | 'versions' = 'markdown';
	let selectedFileLoadVersion = 0;
	let selectedFileOpenPage: number | null = null;
	let selectedFileOpenRevision: number | null = null;
	let moveDocumentLoading = false;
	let moveDocumentTargetPath = '';
	let moveDocumentBrowsePath = '';
	let moveDocumentNewFolderName = '';
	let canEditSelectedFileMetadata = false;

	const toDelimitedValue = (value: string | string[] | null | undefined) =>
		Array.isArray(value) ? value.join(', ') : (value ?? '');
	const toList = (value: string) =>
		value
			.split(',')
			.map((item) => item.trim())
			.filter(Boolean);
	const normalizeFolderPath = (value: string) =>
		value
			.split('/')
			.map((item) => item.trim())
			.filter(Boolean)
			.join('/');
	const getFileTitle = (file: DocumentFile | null | undefined) =>
		file?.meta?.title || file?.meta?.name || file?.filename || file?.name || 'Documento';
	const getFileFolder = (file: DocumentFile | null | undefined) => {
		const folderPath = normalizeFolderPath(file?.meta?.folder_path || '');
		if (folderPath) return folderPath;

		const relativePath = String(file?.meta?.relative_path || '').replace(/^\/+|\/+$/g, '');
		if (relativePath.includes('/')) {
			return relativePath.split('/').slice(0, -1).join('/');
		}

		return '';
	};
	const getFileDocumentType = (file: DocumentFile | null | undefined) => {
		if (file?.meta?.document_type) return file.meta.document_type;
		if (file?.meta?.content_type) return file.meta.content_type;
		const filename = String(file?.filename || file?.name || file?.meta?.name || '');
		if (filename.includes('.')) {
			return filename.split('.').pop()?.toLowerCase() || 'Documento';
		}
		return 'Documento';
	};
	const getFileDocumentStatus = (file: DocumentFile | null | undefined) =>
		file?.meta?.document_status || 'Em elaboração';
	const isFileLockedByStatus = (file: DocumentFile | null | undefined) =>
		Boolean(file?.meta?.is_locked_by_status);
	const getFileLockLabel = (file: DocumentFile | null | undefined) =>
		isFileLockedByStatus(file) ? 'Travado' : 'Editavel';
	const getFileAuthor = (file: DocumentFile | null | undefined) => file?.meta?.author || '';
	const getFileSource = (file: DocumentFile | null | undefined) =>
		file?.meta?.source ||
		file?.meta?.relative_path ||
		file?.filename ||
		file?.name ||
		file?.meta?.name ||
		'—';
	const getActorLabel = (actor: Actor | null | undefined) =>
		actor?.name || actor?.email || actor?.id || '—';
	const getFileUploadActor = (file: DocumentFile | null | undefined) =>
		getActorLabel(file?.meta?.uploaded_by || file?.user);
	const getFileModifiedActor = (file: DocumentFile | null | undefined) =>
		getActorLabel(file?.meta?.last_modified_by || file?.meta?.uploaded_by || file?.user);
	const getFileLastModifiedAt = (file: DocumentFile | null | undefined) =>
		file?.meta?.last_modified_at || file?.updated_at || file?.created_at || null;
	const getFileVersionControl = (file: DocumentFile | null | undefined): VersionControlState => {
		if (file?.meta?.version_control && typeof file.meta.version_control === 'object') {
			return file.meta.version_control as VersionControlState;
		}

		return {
			status: 'available',
			revision: Number(file?.meta?.version || 1) || 1,
			checked_out_by: null,
			checked_out_at: null,
			history: []
		};
	};
	const getFileCheckoutOwner = (file: DocumentFile | null | undefined) =>
		getFileVersionControl(file)?.checked_out_by || null;
	const isFileCheckedOutByCurrentUser = (file: DocumentFile | null | undefined) =>
		getFileVersionControl(file)?.status === 'checked_out' &&
		getFileCheckoutOwner(file)?.id === $user?.id;
	const isFileCheckedOutByOtherUser = (file: DocumentFile | null | undefined) =>
		getFileVersionControl(file)?.status === 'checked_out' &&
		Boolean(getFileCheckoutOwner(file)?.id) &&
		getFileCheckoutOwner(file)?.id !== $user?.id;
	const getFileCheckoutLabel = (file: DocumentFile | null | undefined) => {
		const versionControl = getFileVersionControl(file);
		if (versionControl?.status !== 'checked_out') {
			return '';
		}

		const owner = getFileCheckoutOwner(file);
		return owner?.id === $user?.id ? 'check-out por voce' : `check-out por ${getActorLabel(owner)}`;
	};
	const getFileStatus = (file: DocumentFile | null | undefined) =>
		getFileCheckoutLabel(file) ||
		file?.status ||
		file?.data?.processing_status ||
		file?.data?.status ||
		file?.meta?.processing_status ||
		'ready';
	const getFileTags = (file: DocumentFile | null | undefined): string[] =>
		Array.isArray(file?.meta?.tags) ? file.meta.tags : [];
	const getFolderLabel = (folderPath: string) => {
		const normalized = normalizeFolderPath(folderPath);
		if (!normalized) return 'Raiz';
		const parts = normalized.split('/');
		return parts.at(-1) || normalized;
	};
	const getFolderDepth = (folderPath: string) =>
		normalizeFolderPath(folderPath).split('/').filter(Boolean).length;
	const getFolderParent = (folderPath: string) => {
		const parts = normalizeFolderPath(folderPath).split('/').filter(Boolean);
		if (parts.length <= 1) return '';
		return parts.slice(0, -1).join('/');
	};
	const getFolderDocumentCount = (folderPath: string) =>
		(filteredFileItems ?? []).filter((file) => getFileFolder(file) === folderPath).length;
	const getFolderChildren = (folderPath: string) =>
		(derivedFolders ?? []).filter(
			(folder) => getFolderParent(folder) === normalizeFolderPath(folderPath)
		);
	const getFolderAncestors = (folderPath: string) => {
		const parts = normalizeFolderPath(folderPath).split('/').filter(Boolean);
		return parts.map((_, index) => {
			const path = parts.slice(0, index + 1).join('/');
			return { path, label: parts[index] };
		});
	};
	const expandFolderPaths = (folderPath: string) =>
		getFolderAncestors(folderPath).map((entry) => entry.path);
	const getCollectionFolderRegistry = () => {
		const rawFolders = Array.isArray(knowledge?.meta?.document_folders)
			? (knowledge.meta.document_folders as Array<string | { path?: string }>)
			: [];
		return Array.from(
			new Set(
				rawFolders
					.flatMap((entry) => {
						const path = normalizeFolderPath(typeof entry === 'string' ? entry : entry?.path || '');
						if (!path) return [];
						const segments = path.split('/');
						return segments.map((_, index) => segments.slice(0, index + 1).join('/'));
					})
					.filter(Boolean)
			)
		).sort((a, b) => a.localeCompare(b));
	};
	const matchesQuery = (value: string, searchTerm: string) =>
		value.toLowerCase().includes(searchTerm.toLowerCase());
	const getFileOwnerId = (file: DocumentFile | null | undefined) =>
		file?.meta?.uploaded_by?.id || file?.user?.id || file?.user_id || null;
	const sortFiles = (files: DocumentFile[] = []) => {
		const items = [...files];
		const isAsc = direction === 'asc';

		items.sort((left, right) => {
			if (sortKey === 'name') {
				return isAsc
					? getFileTitle(left).localeCompare(getFileTitle(right))
					: getFileTitle(right).localeCompare(getFileTitle(left));
			}

			if (sortKey === 'created_at') {
				const leftValue = left?.created_at || 0;
				const rightValue = right?.created_at || 0;
				return isAsc ? leftValue - rightValue : rightValue - leftValue;
			}

			const leftValue = left?.updated_at || left?.created_at || 0;
			const rightValue = right?.updated_at || right?.created_at || 0;
			return isAsc ? leftValue - rightValue : rightValue - leftValue;
		});

		if (!sortKey) {
			items.sort(
				(left, right) =>
					(right?.updated_at || right?.created_at || 0) -
					(left?.updated_at || left?.created_at || 0)
			);
		}

		return items;
	};
	const buildChatContextFile = (file: DocumentFile) => ({
		...file,
		type: 'file',
		name: getFileTitle(file),
		filename: file?.filename || file?.name || getFileTitle(file),
		status: file?.status || file?.data?.processing_status || file?.data?.status || 'processed',
		collection_name: knowledge?.name || '',
		meta: {
			...(file?.meta ?? {}),
			name: getFileTitle(file),
			title: file?.meta?.title || getFileTitle(file),
			source: file?.meta?.source || getFileSource(file),
			document_type: file?.meta?.document_type || getFileDocumentType(file),
			document_status: file?.meta?.document_status || getFileDocumentStatus(file),
			author: file?.meta?.author || getFileAuthor(file)
		},
		data: file?.data ?? {}
	});
	const normalizeKnowledgeFilesFallback = (files: Record<string, any>[] = []): DocumentFile[] =>
		(files ?? []).map((file) => ({
			id: file?.id,
			filename: file?.filename || file?.meta?.name || file?.name || 'Documento',
			name: file?.filename || file?.meta?.name || file?.name || 'Documento',
			meta: file?.meta ?? {},
			data: file?.data ?? {},
			created_at: file?.created_at ?? null,
			updated_at: file?.updated_at ?? null,
			user: file?.user ?? null,
			status:
				file?.status ||
				file?.data?.processing_status ||
				file?.data?.status ||
				file?.meta?.processing_status ||
				null,
			itemId: file?.itemId ?? null
		}));
	const isSynthesisRunning = (status?: string | null) =>
		status === 'queued' || status === 'processing';
	const getSynthesisStatusLabel = (status?: string | null) => {
		switch (status) {
			case 'queued':
				return 'Na fila';
			case 'processing':
				return 'Processando';
			case 'completed':
				return 'Concluida';
			case 'completed_with_warnings':
				return 'Concluida com avisos';
			case 'failed':
				return 'Falhou';
			default:
				return 'Sem sintese';
		}
	};
	const getSynthesisStepLabel = (step?: string | null) => {
		switch (step) {
			case 'document_selection':
				return 'Selecionando documentos';
			case 'document_summarization':
				return 'Resumindo documentos';
			case 'document_consolidation':
				return 'Consolidando documentos';
			case 'collection_synthesis':
				return 'Integrando a colecao';
			case 'note_persistence':
				return 'Salvando nota Markdown';
			default:
				return 'Aguardando';
		}
	};
	const getSynthesisStatusTone = (status?: string | null) => {
		if (status === 'failed') return 'text-[#b35a5a] bg-[rgba(233,181,181,0.18)]';
		if (status === 'completed_with_warnings') return 'text-[#9b6a1d] bg-[rgba(237,214,170,0.28)]';
		if (status === 'completed') return 'text-[var(--dochat-accent)] bg-[var(--dochat-accent-soft)]';
		if (isSynthesisRunning(status)) return 'text-[#255f85] bg-[rgba(188,220,241,0.35)]';
		return 'text-[var(--dochat-text-soft)] bg-[rgba(231,225,216,0.52)]';
	};
	const formatSynthesisTimestamp = (value?: number | null) => {
		if (!value) return 'Ainda nao gerada';
		return dayjs(value * 1000).format('DD/MM/YYYY HH:mm');
	};
	const clearSynthesisPoll = () => {
		if (synthesisPollTimer) {
			clearTimeout(synthesisPollTimer);
			synthesisPollTimer = null;
		}
	};
	const scheduleSynthesisPoll = (targetId: string) => {
		if (!browser || !targetId) return;
		clearSynthesisPoll();
		synthesisPollTimer = setTimeout(async () => {
			await loadCollectionSynthesis(targetId, {
				silent: true,
				useStatusEndpoint: true
			});
		}, 2500);
	};
	const applySynthesisPayload = async (
		targetId: string,
		payload: SynthesisReportPayload | null,
		options: {
			silent?: boolean;
			reloadKnowledgeOnCompletion?: boolean;
		} = {}
	) => {
		if (!payload) return;

		const previousStatus = synthesisReport?.status;
		const nextPayload = payload;

		synthesisReport = nextPayload;

		if (isSynthesisRunning(nextPayload.status)) {
			scheduleSynthesisPoll(targetId);
		} else {
			clearSynthesisPoll();
			if (previousStatus && isSynthesisRunning(previousStatus) && !options.silent) {
				if (nextPayload.status === 'failed') {
					toast.error(nextPayload.error || 'A geracao da sintese falhou.');
				} else if (nextPayload.status === 'completed_with_warnings') {
					toast.warning('Sintese concluida com avisos.');
				} else if (nextPayload.status === 'completed') {
					toast.success('Sintese concluida.');
				}
			}

			if (
				options.reloadKnowledgeOnCompletion &&
				previousStatus &&
				isSynthesisRunning(previousStatus) &&
				!isSynthesisRunning(nextPayload.status) &&
				targetId === knowledge?.id
			) {
				await loadKnowledge(targetId);
			}
		}
	};
	const loadCollectionSynthesis = async (
		targetId: string,
		options: {
			silent?: boolean;
			useStatusEndpoint?: boolean;
			reloadKnowledgeOnCompletion?: boolean;
		} = {}
	) => {
		if (!browser || !targetId) return null;

		if (!options.silent) {
			synthesisLoading = true;
		}

		try {
			const fetcher = options.useStatusEndpoint
				? getKnowledgeSynthesisStatusById
				: getKnowledgeSynthesisById;
			const res = await fetcher(localStorage.token, targetId).catch((e) => {
				if (!options.silent) {
					toast.error(`${e}`);
				}
				return null;
			});
			await applySynthesisPayload(targetId, res, {
				silent: options.silent,
				reloadKnowledgeOnCompletion: options.reloadKnowledgeOnCompletion ?? true
			});
			return res;
		} finally {
			if (!options.silent) {
				synthesisLoading = false;
			}
		}
	};
	const toggleSynthesisPanel = () => {
		synthesisCollapsed = !synthesisCollapsed;
	};
	const startCollectionSynthesisHandler = async () => {
		if (!knowledge?.id) return;

		synthesisCollapsed = false;
		synthesisActionLoading = true;
		try {
			const res = await startKnowledgeSynthesisById(localStorage.token, knowledge.id).catch((e) => {
				toast.error(`${e}`);
				return null;
			});
			if (res) {
				await applySynthesisPayload(knowledge.id, res, {
					silent: true,
					reloadKnowledgeOnCompletion: true
				});
			}
		} finally {
			synthesisActionLoading = false;
		}
	};
	const regenerateCollectionSynthesisHandler = async () => {
		if (!knowledge?.id) return;

		synthesisCollapsed = false;
		synthesisActionLoading = true;
		try {
			const res = await regenerateKnowledgeSynthesisById(localStorage.token, knowledge.id).catch(
				(e) => {
					toast.error(`${e}`);
					return null;
				}
			);
			if (res) {
				await applySynthesisPayload(knowledge.id, res, {
					silent: true,
					reloadKnowledgeOnCompletion: true
				});
			}
		} finally {
			synthesisActionLoading = false;
		}
	};
	const mergeFileState = (
		currentFile: DocumentFile | null | undefined,
		nextFile: DocumentFile
	): DocumentFile => ({
		...(currentFile ?? {}),
		...nextFile,
		user: currentFile?.user ?? nextFile?.user ?? null,
		meta: {
			...(currentFile?.meta ?? {}),
			...(nextFile?.meta ?? {})
		},
		data: {
			...(currentFile?.data ?? {}),
			...(nextFile?.data ?? {})
		}
	});
	const syncFileState = (
		nextFile: DocumentFile | null | undefined,
		options: {
			syncSelectedContent?: boolean;
			selectedContent?: string;
		} = {}
	) => {
		if (!nextFile?.id) return;

		let hasUpdatedPersistedItem = false;
		fileItems = (fileItems ?? []).map((file) => {
			if (file?.id !== nextFile.id) return file;
			hasUpdatedPersistedItem = true;
			return mergeFileState(file, nextFile);
		});

		if (!hasUpdatedPersistedItem) {
			fileItems = [...fileItems, nextFile];
		}

		if (knowledge) {
			let hasUpdatedKnowledgeFile = false;
			const nextKnowledgeFiles = (knowledge.files ?? []).map((file) => {
				if (file?.id !== nextFile.id) return file;
				hasUpdatedKnowledgeFile = true;
				return mergeFileState(file, nextFile);
			});

			knowledge = {
				...knowledge,
				files: hasUpdatedKnowledgeFile ? nextKnowledgeFiles : [...nextKnowledgeFiles, nextFile]
			};
		}

		if (!selectedFile?.id || selectedFile.id !== nextFile.id) return;

		selectedFile = mergeFileState(selectedFile, nextFile);

		if (options.selectedContent !== undefined) {
			selectedFileContent = options.selectedContent;
		} else if (options.syncSelectedContent) {
			selectedFileContent = selectedFile?.data?.content || '';
		}
	};
	const invalidateSelectedFileLoad = () => {
		selectedFileLoadVersion += 1;
	};

	let derivedFolders: string[] = [];
	let displayFileItems: DocumentFile[] = [];
	let filteredFileItems: DocumentFile[] = [];
	let visibleFileItems: DocumentFile[] = [];
	let visibleChildFolders: string[] = [];
	let availableDocumentTypes: string[] = [];
	let availableDocumentStatuses: string[] = [];

	let inputFiles: FileList | null = null;

	let query = '';
	let documentTypeFilter = '';
	let documentStatusFilter = '';
	let lockedFilter = '';

	let viewOption = '';
	let sortKey = '';
	let direction = '';

	let fileItems: DocumentFile[] = [];
	let knowledgeLoading = false;
	let pendingFileItems: DocumentFile[] = [];
	let selectedContextDocumentIds: string[] = [];
	let selectedContextDocuments: DocumentFile[] = [];
	let collectionDisplayMode: 'list' | 'table' = 'table';
	let selectedFolder = '';
	let lastLoadedRouteKnowledgeId: string | null = null;
	let lastDeepLinkedSelectionKey: string | null = null;
	let createEditableCopyLoading = false;

	const init = async () => {
		if (knowledgeId) {
			await loadKnowledge(knowledgeId);
		}
	};

	$: if (showLinkedNotesModal && linkedNoteQuery !== undefined) {
		if (linkedNoteSearchDebounceTimer) {
			clearTimeout(linkedNoteSearchDebounceTimer);
		}
		linkedNoteSearchDebounceTimer = setTimeout(() => {
			loadLinkedNoteCandidates();
		}, 250);
	}

	$: derivedFolders = Array.from(
		new Set([
			...getCollectionFolderRegistry(),
			...(knowledge?.folders ?? []),
			...((displayFileItems ?? []).map((file) => getFileFolder(file)).filter(Boolean) ?? [])
		])
	).sort((a, b) => a.localeCompare(b));
	$: availableDocumentTypes = Array.from(
		new Set((displayFileItems ?? []).map((file) => getFileDocumentType(file)).filter(Boolean))
	).sort((a, b) => a.localeCompare(b));
	$: availableDocumentStatuses = Array.from(
		new Set((displayFileItems ?? []).map((file) => getFileDocumentStatus(file)).filter(Boolean))
	).sort((a, b) => a.localeCompare(b));

	$: {
		const persistedFiles = normalizeKnowledgeFilesFallback(fileItems ?? []);
		const persistedIds = new Set(persistedFiles.map((file) => file?.id).filter(Boolean));
		displayFileItems = [
			...pendingFileItems.filter((file) => !(file?.id && persistedIds.has(file.id))),
			...persistedFiles
		];
	}

	$: filteredFileItems = sortFiles(
		(displayFileItems ?? []).filter((file) => {
			const ownerId = getFileOwnerId(file);
			if (viewOption === 'created' && ownerId !== $user?.id) return false;
			if (viewOption === 'shared' && ownerId === $user?.id) return false;
			if (documentTypeFilter && getFileDocumentType(file) !== documentTypeFilter) return false;
			if (documentStatusFilter && getFileDocumentStatus(file) !== documentStatusFilter) {
				return false;
			}
			if (lockedFilter === 'locked' && !isFileLockedByStatus(file)) return false;
			if (lockedFilter === 'editable' && isFileLockedByStatus(file)) return false;

			if (!query?.trim()) return true;

			const haystack = [
				getFileTitle(file),
				getFileFolder(file),
				getFileDocumentType(file),
				getFileDocumentStatus(file),
				getFileLockLabel(file),
				getFileAuthor(file),
				getFileSource(file),
				getFileUploadActor(file),
				getFileModifiedActor(file),
				getFileTags(file).join(' ')
			]
				.join(' ')
				.toLowerCase();

			return matchesQuery(haystack, query.trim());
		})
	);
	$: visibleFileItems = (filteredFileItems ?? []).filter(
		(file) => getFileFolder(file) === selectedFolder
	);
	$: visibleChildFolders = (derivedFolders ?? []).filter((folder) => {
		if (getFolderParent(folder) !== selectedFolder) return false;
		if (!query?.trim()) return true;
		return matchesQuery(`${folder} ${getFolderLabel(folder)}`.toLowerCase(), query.trim());
	});
	$: selectedContextDocuments = (displayFileItems ?? []).filter((file) =>
		selectedContextDocumentIds.includes(file?.id ?? '')
	);
	$: selectedContextDocumentIds = selectedContextDocumentIds.filter((contextId) =>
		(displayFileItems ?? []).some((file) => file?.id === contextId)
	);
	$: canEditSelectedFileContent =
		Boolean(knowledge?.write_access) &&
		Boolean(selectedFile?.id) &&
		!isFileLockedByStatus(selectedFile) &&
		isFileCheckedOutByCurrentUser(selectedFile);
	$: canEditSelectedFileMetadata =
		Boolean(knowledge?.write_access) &&
		Boolean(selectedFile?.id) &&
		!isFileLockedByStatus(selectedFile);
	$: selectedFileCheckoutOwnerLabel = selectedFile
		? getActorLabel(getFileCheckoutOwner(selectedFile))
		: '';

	$: if (selectedFolder !== undefined) {
		invalidateSelectedFileLoad();
		selectedFileId = null;
		selectedFile = null;
		selectedFileTab = 'markdown';
	}

	const hydrateKnowledgeState = (res: Knowledge) => {
		knowledge = res;
		if (!Array.isArray(knowledge?.access_grants)) {
			knowledge.access_grants = [];
		}

		const normalizedItems = normalizeKnowledgeFilesFallback(knowledge?.files ?? []);
		fileItems = normalizedItems;
		knowledgeId = knowledge?.id;

		const persistedIds = new Set(normalizedItems.map((file) => file?.id).filter(Boolean));
		pendingFileItems = pendingFileItems.filter((file) => !(file?.id && persistedIds.has(file.id)));
	};

	const loadKnowledge = async (targetId: string) => {
		if (!browser || !targetId) return null;

		knowledgeLoading = true;
		clearSynthesisPoll();
		id = targetId;
		fileItems = [];
		selectedFileId = null;
		selectedFile = null;
		selectedFileTab = 'markdown';
		selectedFileOpenPage = null;
		selectedFileOpenRevision = null;
		synthesisReport = null;
		synthesisCollapsed = true;

		try {
			const res = await getKnowledgeById(localStorage.token, targetId).catch((e) => {
				toast.error(`${e}`);
				return null;
			});

			if (res) {
				hydrateKnowledgeState(res);
				await loadCollectionSynthesis(targetId, { silent: true });
				lastLoadedRouteKnowledgeId = targetId;
			} else {
				goto('/workspace/knowledge');
			}

			return res;
		} finally {
			knowledgeLoading = false;
		}
	};

	$: if (browser) {
		const routeKnowledgeId = $page.params.id;
		if (routeKnowledgeId && routeKnowledgeId !== lastLoadedRouteKnowledgeId && !knowledgeLoading) {
			loadKnowledge(routeKnowledgeId);
		}
	}

	const applyDeepLinkedFileSelection = async () => {
		if (!browser || knowledgeLoading || !$page.params.id || knowledgeId !== $page.params.id) {
			return;
		}

		const fileId = $page.url.searchParams.get('fileId');
		if (!fileId) {
			lastDeepLinkedSelectionKey = null;
			selectedFileOpenPage = null;
			selectedFileOpenRevision = null;
			return;
		}

		const selectionKey = `${knowledgeId}:${$page.url.searchParams.toString()}`;
		if (selectionKey === lastDeepLinkedSelectionKey) {
			return;
		}

		const file = (displayFileItems ?? []).find((item) => item?.id === fileId);
		if (!file) {
			return;
		}

		const requestedTab = $page.url.searchParams.get('tab');
		const requestedPage = Number.parseInt($page.url.searchParams.get('page') ?? '', 10);
		const requestedRevision = Number.parseInt($page.url.searchParams.get('revision') ?? '', 10);

		selectedFileOpenPage =
			Number.isFinite(requestedPage) && requestedPage > 0 ? requestedPage : null;
		selectedFileOpenRevision =
			Number.isFinite(requestedRevision) && requestedRevision > 0 ? requestedRevision : null;

		selectedFolder = getFileFolder(file);
		await tick();

		selectedFileId = fileId;
		await fileSelectHandler(file, { preserveNavigationHints: true });

		if (requestedTab === 'original' || requestedTab === 'versions' || requestedTab === 'markdown') {
			selectedFileTab = requestedTab;
		} else if (selectedFileOpenPage) {
			selectedFileTab = 'original';
		} else if (selectedFileOpenRevision) {
			selectedFileTab = 'versions';
		}

		lastDeepLinkedSelectionKey = selectionKey;
	};

	$: if (browser && knowledgeId && displayFileItems.length >= 0) {
		void applyDeepLinkedFileSelection();
	}

	const fileSelectHandler = async (
		file: DocumentFile,
		options: { preserveNavigationHints?: boolean } = {}
	) => {
		try {
			if (!options.preserveNavigationHints) {
				selectedFileOpenPage = null;
				selectedFileOpenRevision = null;
				lastDeepLinkedSelectionKey = null;
			}

			const requestVersion = ++selectedFileLoadVersion;
			selectedFile = file;
			selectedFileContent = selectedFile?.data?.content || '';
			selectedFileTab = 'markdown';

			if (!file?.id) return;

			const fullFile = await getFileById(localStorage.token, file.id).catch((e) => {
				toast.error(`${e}`);
				return null;
			});

			if (!fullFile || selectedFileId !== file.id || requestVersion !== selectedFileLoadVersion) {
				return;
			}
			mergeSelectedFileState(fullFile);
			selectedFileContent = fullFile?.data?.content || '';
		} catch (e) {
			toast.error($i18n.t('Failed to load file content.'));
		}
	};

	const mergeSelectedFileState = (nextFile: DocumentFile) => {
		syncFileState(nextFile);
	};

	const openChatWithCollection = async () => {
		if (!knowledge) return;

		const input = {
			prompt: '',
			files: [
				{
					...knowledge,
					type: 'collection',
					status: 'processed'
				}
			],
			selectedToolIds: [],
			selectedFilterIds: [],
			webSearchEnabled: false,
			imageGenerationEnabled: false,
			codeInterpreterEnabled: false
		};

		sessionStorage.setItem('chat-input', JSON.stringify(input));
		await goto('/');
	};

	const toggleContextDocumentSelection = (fileId: string | null | undefined) => {
		if (!fileId) return;
		selectedContextDocumentIds = selectedContextDocumentIds.includes(fileId)
			? selectedContextDocumentIds.filter((id) => id !== fileId)
			: [...selectedContextDocumentIds, fileId];
	};

	const openChatWithDocuments = async (documents: DocumentFile[] = []) => {
		const selectedDocuments = (documents ?? []).filter((file) => file?.id);
		if (selectedDocuments.length === 0) {
			toast.info('Seleciona pelo menos um documento para conversar.');
			return;
		}

		const input = {
			prompt: '',
			files: selectedDocuments.map((file) => buildChatContextFile(file)),
			selectedToolIds: [],
			selectedFilterIds: [],
			webSearchEnabled: false,
			imageGenerationEnabled: false,
			codeInterpreterEnabled: false
		};

		sessionStorage.setItem('chat-input', JSON.stringify(input));
		await goto('/');
	};

	const hydrateDocumentMetadataDraft = (payload: Record<string, any> | DocumentFile | null) => {
		documentMetadataDraft = {
			title:
				payload?.title ||
				payload?.meta?.title ||
				payload?.meta?.name ||
				selectedFile?.meta?.name ||
				'',
			description:
				payload?.description ||
				payload?.summary ||
				payload?.meta?.description ||
				selectedFile?.meta?.description ||
				'',
			summary:
				payload?.summary ||
				payload?.description ||
				payload?.meta?.summary ||
				payload?.meta?.description ||
				'',
			author: payload?.author || payload?.meta?.author || '',
			source: payload?.source || payload?.meta?.source || '',
			language: payload?.language || payload?.meta?.language || '',
			document_type:
				payload?.document_type ||
				payload?.meta?.document_type ||
				selectedFile?.meta?.document_type ||
				'',
			document_status:
				payload?.document_status ||
				payload?.meta?.document_status ||
				selectedFile?.meta?.document_status ||
				'Em elaboração',
			version: String(payload?.version || payload?.meta?.version || '1'),
			tags: toDelimitedValue(payload?.tags || payload?.meta?.tags || selectedFile?.meta?.tags),
			entities: toDelimitedValue(
				payload?.entities || payload?.meta?.entities || selectedFile?.meta?.entities
			),
			suggested_collection_hints: toDelimitedValue(payload?.suggested_collection_hints),
			folder_path: payload?.meta?.folder_path || selectedFile?.meta?.folder_path || ''
		};
	};

	const hydrateCollectionMetadataDraft = () => {
		const baseFile = selectedFile ?? visibleFileItems?.[0] ?? null;
		collectionMetadataDraft = {
			description: baseFile?.meta?.description || '',
			summary: baseFile?.meta?.summary || '',
			author: baseFile?.meta?.author || '',
			source: baseFile?.meta?.source || '',
			language: baseFile?.meta?.language || '',
			document_type: baseFile?.meta?.document_type || '',
			document_status: baseFile?.meta?.document_status || 'Em elaboração',
			version: String(baseFile?.meta?.version || ''),
			tags: toDelimitedValue(baseFile?.meta?.tags),
			entities: toDelimitedValue(baseFile?.meta?.entities),
			folder_path: selectedFolder || ''
		};
	};

	const persistCollectionFolders = async (paths: string[] = []) => {
		if (!knowledge?.id || paths.length === 0) return null;

		const nextFolders = Array.from(
			new Set(
				[...getCollectionFolderRegistry(), ...paths.flatMap((path) => expandFolderPaths(path))]
					.map((path) => normalizeFolderPath(path))
					.filter(Boolean)
			)
		).sort((a, b) => a.localeCompare(b));

		const res = await updateKnowledgeById(localStorage.token, knowledge.id, {
			name: knowledge.name,
			description: knowledge.description,
			access_grants: knowledge.access_grants ?? [],
			meta: {
				...(knowledge?.meta ?? {}),
				document_folders: nextFolders
			}
		}).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			hydrateKnowledgeState(res);
		}

		return res;
	};

	const openDocumentMetadataModal = () => {
		if (!selectedFile) return;
		documentMetadataInstruction = '';
		hydrateDocumentMetadataDraft(selectedFile);
		showDocumentMetadataModal = true;
	};

	const openMoveDocumentModal = (file: DocumentFile | null = selectedFile) => {
		if (!file?.id || !knowledge?.write_access || isFileLockedByStatus(file)) return;

		const currentFolder = getFileFolder(file);
		moveDocumentTargetPath = currentFolder;
		moveDocumentBrowsePath = currentFolder;
		moveDocumentNewFolderName = '';
		showMoveDocumentModal = true;
	};

	const openCollectionMetadataModal = () => {
		collectionMetadataInstruction = '';
		hydrateCollectionMetadataDraft();
		showCollectionMetadataModal = true;
	};

	const createMoveTargetFolderHandler = async () => {
		const normalizedName = normalizeFolderPath(moveDocumentNewFolderName);
		if (!normalizedName) return;

		const nextFolderPath =
			moveDocumentBrowsePath && !String(moveDocumentNewFolderName).includes('/')
				? normalizeFolderPath(`${moveDocumentBrowsePath}/${normalizedName}`)
				: normalizedName;

		const res = await persistCollectionFolders([nextFolderPath]);
		if (!res) return;

		moveDocumentBrowsePath = nextFolderPath;
		moveDocumentTargetPath = nextFolderPath;
		moveDocumentNewFolderName = '';
		toast.success('Pasta criada para mover o documento.');
	};

	const recoverSelectedFileCheckoutHandler = async () => {
		if (
			!selectedFile?.id ||
			!knowledge?.write_access ||
			checkoutActionLoading ||
			isFileCheckedOutByOtherUser(selectedFile) ||
			isFileLockedByStatus(selectedFile)
		) {
			return null;
		}

		checkoutActionLoading = true;
		try {
			const res = await checkoutFileById(localStorage.token, selectedFile.id).catch(() => null);
			if (res) {
				syncFileState(res, {
					selectedContent: selectedFileContent
				});
			}
			return res;
		} finally {
			checkoutActionLoading = false;
		}
	};

	const moveSelectedFileHandler = async () => {
		if (!selectedFile?.id || moveDocumentLoading) return;

		const targetFolderPath = normalizeFolderPath(moveDocumentTargetPath);
		const currentFolderPath = getFileFolder(selectedFile);

		if (targetFolderPath === currentFolderPath) {
			toast.info('O documento ja esta nesta pasta.');
			showMoveDocumentModal = false;
			return;
		}

		moveDocumentLoading = true;

		try {
			const res = await updateDocumentMetadataById(localStorage.token, selectedFile.id, {
				meta: {
					folder_path: targetFolderPath || null
				}
			}).catch((e) => {
				toast.error(`${e}`);
				return null;
			});

			if (!res) return;

			if (targetFolderPath) {
				await persistCollectionFolders([targetFolderPath]);
			}

			const refreshedFile = await getFileById(localStorage.token, selectedFile.id).catch(
				() => null
			);
			if (refreshedFile) {
				invalidateSelectedFileLoad();
				syncFileState(refreshedFile, {
					selectedContent: selectedFileContent
				});
			} else {
				invalidateSelectedFileLoad();
				syncFileState(
					{
						...selectedFile,
						meta: {
							...(selectedFile?.meta ?? {}),
							...(res?.metadata ?? {}),
							folder_path: targetFolderPath || null
						}
					},
					{
						selectedContent: selectedFileContent
					}
				);
			}

			showMoveDocumentModal = false;
			toast.success(
				targetFolderPath
					? `Documento movido para ${targetFolderPath}.`
					: 'Documento movido para a raiz.'
			);
		} finally {
			moveDocumentLoading = false;
		}
	};

	const generateDocumentMetadataHandler = async () => {
		if (!selectedFile?.id) return;
		documentMetadataLoading = true;

		const res = await suggestDocumentMetadataById(localStorage.token, selectedFile.id, {
			instruction: documentMetadataInstruction
		}).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			hydrateDocumentMetadataDraft(res);
			toast.success($i18n.t('Metadata suggestion generated.'));
		}

		documentMetadataLoading = false;
	};

	const saveDocumentMetadataHandler = async () => {
		if (!selectedFile?.id) return;
		if (!canEditSelectedFileMetadata) {
			toast.info('Este documento esta travado. Crie uma copia editavel para continuar.');
			return;
		}
		documentMetadataSaving = true;

		const res = await updateDocumentMetadataById(localStorage.token, selectedFile.id, {
			title: documentMetadataDraft.title,
			description: documentMetadataDraft.description,
			summary: documentMetadataDraft.summary,
			author: documentMetadataDraft.author,
			source: documentMetadataDraft.source,
			language: documentMetadataDraft.language,
			document_type: documentMetadataDraft.document_type,
			document_status: documentMetadataDraft.document_status,
			version: documentMetadataDraft.version,
			tags: toList(documentMetadataDraft.tags),
			entities: toList(documentMetadataDraft.entities),
			suggested_collection_hints: toList(documentMetadataDraft.suggested_collection_hints),
			meta: {
				folder_path: normalizeFolderPath(documentMetadataDraft.folder_path)
			}
		}).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			const normalizedFolderPath = normalizeFolderPath(documentMetadataDraft.folder_path);
			selectedFile = {
				...selectedFile,
				meta: {
					...(selectedFile?.meta ?? {}),
					...(res?.metadata ?? {}),
					name: res.title,
					title: res.title,
					description: res.description,
					summary: res.summary,
					tags: res.tags,
					entities: res.entities,
					author: res.author,
					source: res.source,
					language: res.language,
					document_type: res.document_type,
					document_status: res.document_status,
					is_locked_by_status: res.is_locked_by_status,
					locked_reason: res.locked_reason,
					copied_from_document_id: res.copied_from_document_id,
					version: res.version,
					folder_path: res?.metadata?.folder_path || normalizedFolderPath
				}
			};

			if (normalizedFolderPath) {
				await persistCollectionFolders([normalizedFolderPath]);
			}

			toast.success($i18n.t('Metadata saved.'));
			await init();
		}

		documentMetadataSaving = false;
	};

	const createEditableCopyHandler = async () => {
		if (!selectedFile?.id || createEditableCopyLoading) return;

		createEditableCopyLoading = true;
		try {
			const res = await createEditableDocumentCopyById(localStorage.token, selectedFile.id).catch(
				(e) => {
					toast.error(`${e}`);
					return null;
				}
			);

			if (!res) return;

			await loadKnowledge(knowledge?.id ?? id ?? '');

			const copiedFile = normalizeKnowledgeFilesFallback(knowledge?.files ?? []).find(
				(file) => file?.id === res.id
			);
			if (copiedFile) {
				selectedFolder = getFileFolder(copiedFile);
				selectedFileId = copiedFile.id ?? null;
				fileSelectHandler(copiedFile);
			}

			showDocumentMetadataModal = false;
			toast.success('Copia editavel criada com sucesso.');
		} finally {
			createEditableCopyLoading = false;
		}
	};

	const loadLinkedNoteCandidates = async () => {
		if (!showLinkedNotesModal) return;
		linkedNotesLoading = true;

		const res = await searchNotes(
			localStorage.token,
			linkedNoteQuery || null,
			null,
			null,
			'updated_at',
			1
		).catch(() => null);

		linkedNoteCandidates = res?.items ?? [];
		linkedNotesLoading = false;
	};

	const addLinkedNoteHandler = async (note: LinkedNote) => {
		if (!id) return;
		const res = await addNoteToKnowledgeById(localStorage.token, id, note.id).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			knowledge = res;
			showLinkedNotesModal = false;
			linkedNoteQuery = '';
			toast.success($i18n.t('Note linked to collection.'));
		}
	};

	const removeLinkedNoteHandler = async (noteId: string) => {
		if (!id) return;
		const res = await removeNoteFromKnowledgeById(localStorage.token, id, noteId).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			knowledge = res;
			toast.success($i18n.t('Note removed from collection.'));
		}
	};

	const createFileFromText = (name: string, content: string): File => {
		const blob = new Blob([content], { type: 'text/plain' });
		const file = blobToFile(blob, `${name}.txt`);

		console.log(file);
		return file;
	};

	const getAllCollectionFiles = async () => {
		return normalizeKnowledgeFilesFallback(knowledge?.files ?? []).filter((file) => file?.id);
	};

	const applyMetadataToCollection = async (generateWithAI = false) => {
		if (!knowledge?.id) return;

		collectionMetadataLoading = generateWithAI;
		collectionMetadataSaving = !generateWithAI;

		try {
			const targetFiles = await getAllCollectionFiles();
			const folderPath = normalizeFolderPath(collectionMetadataDraft.folder_path);
			const sharedTags = toList(collectionMetadataDraft.tags);
			const sharedEntities = toList(collectionMetadataDraft.entities);

			if (!targetFiles.length) {
				toast.info('Nao ha documentos na colecao para atualizar.');
				return;
			}

			let processed = 0;
			for (const file of targetFiles) {
				if (!file.id) continue;

				let payload: Record<string, any> = {
					description: collectionMetadataDraft.description || undefined,
					summary: collectionMetadataDraft.summary || undefined,
					author: collectionMetadataDraft.author || undefined,
					source: collectionMetadataDraft.source || undefined,
					language: collectionMetadataDraft.language || undefined,
					document_type: collectionMetadataDraft.document_type || undefined,
					version: collectionMetadataDraft.version || undefined,
					tags: sharedTags.length > 0 ? sharedTags : undefined,
					entities: sharedEntities.length > 0 ? sharedEntities : undefined,
					meta: {
						folder_path: folderPath || undefined
					}
				};

				if (generateWithAI) {
					const suggestion = await suggestDocumentMetadataById(localStorage.token, file.id, {
						instruction: collectionMetadataInstruction
					}).catch(() => null);

					if (suggestion) {
						payload = {
							title: suggestion.title || undefined,
							description: suggestion.description || payload.description,
							summary: suggestion.summary || payload.summary,
							author: suggestion.author || payload.author,
							source: suggestion.source || payload.source,
							language: suggestion.language || payload.language,
							document_type: suggestion.document_type || payload.document_type,
							version: collectionMetadataDraft.version || file?.meta?.version || undefined,
							tags: sharedTags.length > 0 ? sharedTags : (suggestion.tags ?? []),
							entities: sharedEntities.length > 0 ? sharedEntities : (suggestion.entities ?? []),
							meta: {
								folder_path: folderPath || file?.meta?.folder_path || undefined
							}
						};
					}
				}

				await updateDocumentMetadataById(localStorage.token, file.id, payload).catch((e) => {
					toast.error(`${e}`);
					return null;
				});

				processed += 1;
			}

			toast.success(
				generateWithAI
					? `${processed} documentos receberam metadados com IA.`
					: `${processed} documentos foram atualizados.`
			);
			if (folderPath) {
				await persistCollectionFolders([folderPath]);
			}
			showCollectionMetadataModal = false;
			await init();
		} finally {
			collectionMetadataLoading = false;
			collectionMetadataSaving = false;
		}
	};

	const createFolderHandler = async () => {
		const value = window.prompt(
			selectedFolder ? `Nome da subpasta dentro de ${selectedFolder}` : 'Nome da pasta',
			''
		);
		if (!value) return;

		const normalizedValue = normalizeFolderPath(value);
		const normalized =
			selectedFolder && !String(value).includes('/')
				? normalizeFolderPath(`${selectedFolder}/${normalizedValue}`)
				: normalizedValue;
		if (!normalized) return;

		const res = await persistCollectionFolders([normalized]);
		if (!res) return;
		selectedFolder = normalized;
		toast.success('Pasta criada com sucesso.');
	};

	const uploadWeb = async (urls: string | string[]) => {
		if (!Array.isArray(urls)) {
			urls = [urls];
		}

		const targetFolderPath = normalizeFolderPath(selectedFolder);

		const newFileItems = urls.map((url) => ({
			type: 'file',
			file: '',
			id: null,
			url: url,
			name: url,
			size: null,
			status: 'uploading',
			error: '',
			itemId: createTempId(),
			meta: {
				folder_path: targetFolderPath || undefined
			}
		}));

		// Display all items at once without overwriting persisted collection files.
		pendingFileItems = [...newFileItems, ...pendingFileItems];

		for (const fileItem of newFileItems) {
			try {
				console.log(fileItem);
				const res = await processWeb(localStorage.token, '', fileItem.url, false).catch((e) => {
					console.error('Error processing web URL:', e);
					return null;
				});

				if (res) {
					console.log(res);
					const file = createFileFromText(
						// Use URL as filename, sanitized
						fileItem.url
							.replace(/[^a-z0-9]/gi, '_')
							.toLowerCase()
							.slice(0, 50),
						res.content
					);

					const uploadedFile = await uploadFile(
						localStorage.token,
						file,
						targetFolderPath ? { folder_path: targetFolderPath } : null,
						false
					).catch((e) => {
						toast.error(`${e}`);
						return null;
					});

					if (uploadedFile) {
						console.log(uploadedFile);
						pendingFileItems = pendingFileItems.map((item) => {
							if (item.itemId === fileItem.itemId) {
								item.id = uploadedFile.id;
							}
							return item;
						});

						if (uploadedFile.error) {
							console.warn('File upload warning:', uploadedFile.error);
							toast.warning(uploadedFile.error);
							pendingFileItems = pendingFileItems.filter((file) => file.id !== uploadedFile.id);
						} else {
							await addFileHandler(uploadedFile.id);
						}
					} else {
						toast.error($i18n.t('Failed to upload file.'));
					}
				} else {
					// remove the item from fileItems
					pendingFileItems = pendingFileItems.filter((item) => item.itemId !== fileItem.itemId);
					toast.error($i18n.t('Failed to process URL: {{url}}', { url: fileItem.url }));
				}
			} catch (e) {
				// remove the item from fileItems
				pendingFileItems = pendingFileItems.filter((item) => item.itemId !== fileItem.itemId);
				toast.error(`${e}`);
			}
		}
	};

	const uploadFileHandler = async (
		file: File,
		options: { directorySync?: boolean; relativePath?: string } = {}
	) => {
		console.log(file);
		if (!knowledge?.id) return null;
		const targetFolderPath = normalizeFolderPath(selectedFolder);
		const maxFileSizeMb = (($config as any)?.file?.max_size ?? null) as number | null;

		const fileItem = {
			type: 'file',
			file: '',
			id: null,
			url: '',
			name: file.name,
			size: file.size,
			status: 'uploading',
			error: '',
			itemId: createTempId(),
			meta: {
				folder_path: options.directorySync ? undefined : targetFolderPath || undefined,
				relative_path: options.relativePath || undefined
			}
		};

		if (fileItem.size == 0) {
			toast.error($i18n.t('You cannot upload an empty file.'));
			return null;
		}

		if (maxFileSizeMb !== null && file.size > maxFileSizeMb * 1024 * 1024) {
			console.log('File exceeds max size limit:', {
				fileSize: file.size,
				maxSize: maxFileSizeMb * 1024 * 1024
			});
			toast.error(
				$i18n.t(`File size should not exceed {{maxSize}} MB.`, {
					maxSize: maxFileSizeMb
				})
			);
			return;
		}

		pendingFileItems = [fileItem, ...pendingFileItems];
		try {
			let metadata: Record<string, any> = {
				knowledge_id: knowledge.id,
				...(options.directorySync
					? {
							source_type: 'directory_sync',
							relative_path: options.relativePath || file.name
						}
					: {
							folder_path: targetFolderPath || undefined
						}),
				// If the file is an audio file, provide the language for STT.
				...((file.type.startsWith('audio/') || file.type.startsWith('video/')) &&
				$settings?.audio?.stt?.language
					? {
							language: $settings?.audio?.stt?.language
						}
					: {})
			};

			const uploadedFile = await uploadFile(localStorage.token, file, metadata, false).catch(
				(e) => {
					toast.error(`${e}`);
					return null;
				}
			);

			if (uploadedFile) {
				console.log(uploadedFile);
				pendingFileItems = pendingFileItems.map((item) => {
					if (item.itemId === fileItem.itemId) {
						item.id = uploadedFile.id;
					}
					return item;
				});

				if (uploadedFile.error) {
					console.warn('File upload warning:', uploadedFile.error);
					toast.warning(uploadedFile.error);
					pendingFileItems = pendingFileItems.filter((file) => file.id !== uploadedFile.id);
				} else {
					if (options.directorySync) {
						const syncRes = await upsertDirectorySyncFileToKnowledge(
							localStorage.token,
							knowledge.id,
							uploadedFile.id,
							options.relativePath || file.name
						).catch((e) => {
							toast.error(`${e}`);
							return null;
						});

						if (syncRes) {
							knowledge = syncRes;
							await init();
						}
					} else {
						await addFileHandler(uploadedFile.id);
					}
				}
			} else {
				toast.error($i18n.t('Failed to upload file.'));
			}
		} catch (e) {
			pendingFileItems = pendingFileItems.filter((item) => item.itemId !== fileItem.itemId);
			toast.error(`${e}`);
		}
	};

	const uploadDirectoryHandler = async (syncMode = false) => {
		// Check if File System Access API is supported
		const isFileSystemAccessSupported = 'showDirectoryPicker' in window;

		try {
			if (isFileSystemAccessSupported) {
				// Modern browsers (Chrome, Edge) implementation
				await handleModernBrowserUpload(syncMode);
			} else {
				// Firefox fallback
				await handleFirefoxUpload(syncMode);
			}
		} catch (error) {
			handleUploadError(error);
		}
	};

	// Helper function to check if a path contains hidden folders
	const hasHiddenFolder = (path: string) => {
		return path.split('/').some((part) => part.startsWith('.'));
	};

	// Modern browsers implementation using File System Access API
	const handleModernBrowserUpload = async (syncMode = false) => {
		const dirHandle: any = await (window as any).showDirectoryPicker();
		let totalFiles = 0;
		let uploadedFiles = 0;

		// Function to update the UI with the progress
		const updateProgress = () => {
			const percentage = (uploadedFiles / totalFiles) * 100;
			toast.info(
				$i18n.t('Upload Progress: {{uploadedFiles}}/{{totalFiles}} ({{percentage}}%)', {
					uploadedFiles: uploadedFiles,
					totalFiles: totalFiles,
					percentage: percentage.toFixed(2)
				})
			);
		};

		// Recursive function to count all files excluding hidden ones
		async function countFiles(dirHandle: any): Promise<void> {
			for await (const entry of dirHandle.values()) {
				// Skip hidden files and directories
				if (entry.name.startsWith('.')) continue;

				if (entry.kind === 'file') {
					totalFiles++;
				} else if (entry.kind === 'directory') {
					// Only process non-hidden directories
					if (!entry.name.startsWith('.')) {
						await countFiles(entry);
					}
				}
			}
		}

		// Recursive function to process directories excluding hidden files and folders
		async function processDirectory(dirHandle: any, path = ''): Promise<void> {
			for await (const entry of dirHandle.values()) {
				// Skip hidden files and directories
				if (entry.name.startsWith('.')) continue;

				const entryPath = path ? `${path}/${entry.name}` : entry.name;

				// Skip if the path contains any hidden folders
				if (hasHiddenFolder(entryPath)) continue;

				if (entry.kind === 'file') {
					const file = await entry.getFile();
					const fileWithPath = new File([file], entryPath, { type: file.type });

					await uploadFileHandler(fileWithPath, {
						directorySync: syncMode,
						relativePath: entryPath
					});
					uploadedFiles++;
					updateProgress();
				} else if (entry.kind === 'directory') {
					// Only process non-hidden directories
					if (!entry.name.startsWith('.')) {
						await processDirectory(entry, entryPath);
					}
				}
			}
		}

		await countFiles(dirHandle);
		updateProgress();

		if (totalFiles > 0) {
			await processDirectory(dirHandle);
		} else {
			console.log('No files to upload.');
		}
	};

	// Firefox fallback implementation using traditional file input
	const handleFirefoxUpload = async (syncMode = false) => {
		return new Promise<void>((resolve, reject) => {
			// Create hidden file input
			const input = document.createElement('input') as HTMLInputElement & {
				directory?: boolean;
				webkitdirectory?: boolean;
			};
			input.type = 'file';
			input.webkitdirectory = true;
			input.directory = true;
			input.multiple = true;
			input.style.display = 'none';

			// Add input to DOM temporarily
			document.body.appendChild(input);

			input.onchange = async () => {
				try {
					const files = Array.from(input.files ?? [])
						// Filter out files from hidden folders
						.filter((file) => !hasHiddenFolder(file.webkitRelativePath));

					let totalFiles = files.length;
					let uploadedFiles = 0;

					// Function to update the UI with the progress
					const updateProgress = () => {
						const percentage = (uploadedFiles / totalFiles) * 100;
						toast.info(
							$i18n.t('Upload Progress: {{uploadedFiles}}/{{totalFiles}} ({{percentage}}%)', {
								uploadedFiles: uploadedFiles,
								totalFiles: totalFiles,
								percentage: percentage.toFixed(2)
							})
						);
					};

					updateProgress();

					// Process all files
					for (const file of files) {
						// Skip hidden files (additional check)
						if (!file.name.startsWith('.')) {
							const relativePath = file.webkitRelativePath || file.name;
							const fileWithPath = new File([file], relativePath, { type: file.type });

							await uploadFileHandler(fileWithPath, {
								directorySync: syncMode,
								relativePath
							});
							uploadedFiles++;
							updateProgress();
						}
					}

					// Clean up
					document.body.removeChild(input);
					resolve();
				} catch (error) {
					reject(error);
				}
			};

			input.onerror = (error) => {
				document.body.removeChild(input);
				reject(error);
			};

			// Trigger file picker
			input.click();
		});
	};

	// Error handler
	const handleUploadError = (error: any) => {
		if (error.name === 'AbortError') {
			toast.info($i18n.t('Directory selection was cancelled'));
		} else {
			toast.error($i18n.t('Error accessing directory'));
			console.error('Directory access error:', error);
		}
	};

	// Helper function to maintain file paths within zip
	const syncDirectoryHandler = async () => {
		await uploadDirectoryHandler(true);
	};

	const addFileHandler = async (fileId: string) => {
		if (!id) return;
		const res = await addFileToKnowledgeById(localStorage.token, id, fileId).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			knowledge = res;
			fileItems = normalizeKnowledgeFilesFallback(res?.files ?? []);
			pendingFileItems = pendingFileItems.filter((file) => file.id !== fileId);
			toast.success($i18n.t('File added successfully.'));
			await init();
		} else {
			toast.error($i18n.t('Failed to add file.'));
			pendingFileItems = pendingFileItems.filter((file) => file.id !== fileId);
		}
	};

	const deleteFileHandler = async (fileId: string) => {
		if (!id) return;
		try {
			console.log('Starting file deletion process for:', fileId);

			// Remove from knowledge base only
			const res = await removeFileFromKnowledgeById(localStorage.token, id, fileId);
			console.log('Knowledge base updated:', res);

			if (res) {
				toast.success($i18n.t('File removed successfully.'));
				await init();
			}
		} catch (e) {
			console.error('Error in deleteFileHandler:', e);
			toast.error(`${e}`);
		}
	};

	const checkoutSelectedFileHandler = async () => {
		if (!selectedFile?.id || checkoutActionLoading || !knowledge?.write_access) return;
		if (isFileLockedByStatus(selectedFile)) {
			toast.info('Este documento esta travado. Crie uma copia editavel para continuar.');
			return;
		}

		checkoutActionLoading = true;
		try {
			const res = await checkoutFileById(localStorage.token, selectedFile.id).catch((e) => {
				toast.error(`${e}`);
				return null;
			});

			if (res) {
				invalidateSelectedFileLoad();
				syncFileState(res, {
					selectedContent: selectedFileContent
				});
				toast.success('Check-out realizado. Ja pode editar este documento.');
			}
		} finally {
			checkoutActionLoading = false;
		}
	};

	let debounceTimeout: ReturnType<typeof setTimeout> | null = null;
	let dragged = false;
	let isSaving = false;
	let checkoutActionLoading = false;

	const updateFileContentHandler = async () => {
		if (!selectedFile?.id) return;
		if (isFileLockedByStatus(selectedFile)) {
			toast.info('Este documento esta travado. Crie uma copia editavel para continuar.');
			return;
		}

		if (isSaving) {
			console.log('Save operation already in progress, skipping...');
			return;
		}

		if (!canEditSelectedFileContent) {
			const recoveredCheckout = await recoverSelectedFileCheckoutHandler();
			if (!recoveredCheckout && !canEditSelectedFileContent) {
				toast.info('Faca check-out do documento antes de salvar alteracoes.');
				return;
			}
		}

		isSaving = true;

		try {
			let checkinError: string | null = null;
			let res = await checkinFileById(
				localStorage.token,
				selectedFile.id,
				selectedFileContent
			).catch((e) => {
				checkinError = String(e ?? '');
				return null;
			});

			const normalizedCheckinError = String(checkinError ?? '').toLowerCase();
			if (!res && normalizedCheckinError.includes('check-out do documento')) {
				const recoveredCheckout = await recoverSelectedFileCheckoutHandler();
				if (!recoveredCheckout) {
					toast.error(checkinError ?? 'Faca check-out do documento antes de salvar alteracoes.');
					return;
				}

				checkinError = null;
				res = await checkinFileById(localStorage.token, selectedFile.id, selectedFileContent).catch(
					(e) => {
						checkinError = String(e ?? '');
						return null;
					}
				);
			}

			if (!res && checkinError) {
				toast.error(checkinError);
				return;
			}

			if (res) {
				invalidateSelectedFileLoad();
				syncFileState(res, {
					selectedContent: selectedFileContent
				});
				toast.success('Check-in concluido. Alteracoes salvas e documento liberado.');
			}
		} finally {
			isSaving = false;
		}
	};

	const changeDebounceHandler = () => {
		if (!knowledge || !id) return;
		const currentKnowledge = knowledge;
		const currentKnowledgeId = id;

		console.log('debounce');
		if (debounceTimeout) {
			clearTimeout(debounceTimeout);
		}

		debounceTimeout = setTimeout(async () => {
			if (currentKnowledge.name.trim() === '' || currentKnowledge.description.trim() === '') {
				toast.error($i18n.t('Please fill in all fields.'));
				return;
			}

			const res = await updateKnowledgeById(localStorage.token, currentKnowledgeId, {
				...currentKnowledge,
				name: currentKnowledge.name,
				description: currentKnowledge.description,
				access_grants: currentKnowledge.access_grants ?? []
			}).catch((e) => {
				toast.error(`${e}`);
			});

			if (res) {
				toast.success($i18n.t('Knowledge updated successfully'));
			}
		}, 1000);
	};

	const onDragOver = (e: DragEvent) => {
		e.preventDefault();

		// Check if a file is being draggedOver.
		if (e.dataTransfer?.types?.includes('Files')) {
			dragged = true;
		} else {
			dragged = false;
		}
	};

	const onDragLeave = () => {
		dragged = false;
	};

	const onDrop = async (e: DragEvent) => {
		e.preventDefault();
		dragged = false;

		if (!knowledge?.write_access) {
			toast.error($i18n.t('You do not have permission to upload files to this knowledge base.'));
			return;
		}

		const handleUploadingFileFolder = (items: ArrayLike<any>) => {
			for (const item of Array.from(items)) {
				if (item.isFile) {
					item.file((file: File | null) => {
						if (file) {
							void uploadFileHandler(file);
						}
					});
					continue;
				}

				// Not sure why you have to call webkitGetAsEntry and isDirectory seperate, but it won't work if you try item.webkitGetAsEntry().isDirectory
				const wkentry = item.webkitGetAsEntry();
				const isDirectory = wkentry.isDirectory;
				if (isDirectory) {
					// Read the directory
					wkentry.createReader().readEntries(
						(entries: any[]) => {
							handleUploadingFileFolder(entries);
						},
						(error: any) => {
							console.error('Error reading directory entries:', error);
						}
					);
				} else {
					toast.info($i18n.t('Uploading file...'));
					const droppedFile = item.getAsFile();
					if (droppedFile) {
						void uploadFileHandler(droppedFile);
					}
					toast.success($i18n.t('File uploaded!'));
				}
			}
		};

		if (e.dataTransfer?.types?.includes('Files')) {
			if (e.dataTransfer?.files) {
				const inputItems = e.dataTransfer?.items;

				if (inputItems && inputItems.length > 0) {
					handleUploadingFileFolder(Array.from(inputItems));
				} else {
					toast.error($i18n.t(`File not found.`));
				}
			}
		}
	};

	const handleAddContentUpload = (data: { type: string }) => {
		if (data.type === 'directory') {
			void uploadDirectoryHandler();
		} else if (data.type === 'web') {
			showAddWebpageModal = true;
		} else if (data.type === 'text') {
			showAddTextContentModal = true;
		} else {
			(document.getElementById('files-input') as HTMLInputElement | null)?.click();
		}
	};

	const saveKnowledgeAccessGrantsHandler = async () => {
		if (!knowledge?.id) return;

		try {
			await updateKnowledgeAccessGrants(
				localStorage.token,
				knowledge.id,
				knowledge.access_grants ?? []
			);
			toast.success($i18n.t('Saved'));
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	onMount(async () => {
		const dropZone = document.querySelector('body');
		dropZone?.addEventListener('dragover', onDragOver);
		dropZone?.addEventListener('drop', onDrop);
		dropZone?.addEventListener('dragleave', onDragLeave);
	});

	onDestroy(() => {
		if (linkedNoteSearchDebounceTimer) {
			clearTimeout(linkedNoteSearchDebounceTimer);
		}
		clearSynthesisPoll();
		const dropZone = document.querySelector('body');
		dropZone?.removeEventListener('dragover', onDragOver);
		dropZone?.removeEventListener('drop', onDrop);
		dropZone?.removeEventListener('dragleave', onDragLeave);
	});
</script>

<FilesOverlay show={dragged} />
<SyncConfirmDialog
	bind:show={showSyncConfirmModal}
	message={$i18n.t(
		'This will sync directory files without removing manually uploaded documents. Do you wish to continue?'
	)}
	on:confirm={() => {
		syncDirectoryHandler();
	}}
/>

<AttachWebpageModal
	bind:show={showAddWebpageModal}
	onSubmit={async (e) => {
		uploadWeb(e.data);
	}}
/>

<AddTextContentModal
	bind:show={showAddTextContentModal}
	on:submit={(e) => {
		const file = createFileFromText(e.detail.name, e.detail.content);
		uploadFileHandler(file);
	}}
/>

<input
	id="files-input"
	bind:files={inputFiles}
	type="file"
	multiple
	hidden
	on:change={async () => {
		if (inputFiles && inputFiles.length > 0) {
			for (const file of inputFiles) {
				await uploadFileHandler(file);
			}

			inputFiles = null;
			const fileInputElement = document.getElementById('files-input') as HTMLInputElement | null;

			if (fileInputElement) {
				fileInputElement.value = '';
			}
		} else {
			toast.error($i18n.t(`File not found.`));
		}
	}}
/>

<div class="flex flex-col w-full h-full min-h-full" id="collection-container">
	{#if id && knowledge}
		<AccessControlModal
			bind:show={showAccessControlModal}
			bind:accessGrants={knowledge.access_grants}
			share={$user?.permissions?.sharing?.knowledge || $user?.role === 'admin'}
			sharePublic={$user?.permissions?.sharing?.public_knowledge || $user?.role === 'admin'}
			shareUsers={($user?.permissions?.access_grants?.allow_users ?? true) ||
				$user?.role === 'admin'}
			onChange={saveKnowledgeAccessGrantsHandler}
			accessRoles={['read', 'write']}
		/>
		{#if showLinkedNotesModal}
			<div
				class="fixed inset-0 z-40 flex items-center justify-center p-4 bg-black/15 backdrop-blur-sm"
			>
				<div
					class="w-full max-w-2xl rounded-[1.5rem] border border-[rgba(221,214,202,0.92)] bg-white p-4 shadow-[0_24px_64px_rgba(84,74,58,0.12)]"
				>
					<div class="flex items-start justify-between gap-4">
						<div>
							<div
								class="text-[0.72rem] font-bold uppercase tracking-[0.08em] text-[var(--dochat-accent)]"
							>
								Notas vinculadas
							</div>
							<div class="mt-1 text-lg font-semibold text-[var(--dochat-text)]">
								Adicionar nota a {knowledge.name}
							</div>
							<div class="mt-1 text-sm text-[var(--dochat-text-soft)]">
								A nota continua viva: novas edicoes passam a alimentar a colecao.
							</div>
						</div>

						<button
							type="button"
							class="rounded-full p-2 text-[var(--dochat-text-faint)] hover:bg-[var(--dochat-bg)] hover:text-[var(--dochat-accent)]"
							aria-label={$i18n.t('Close')}
							on:click={() => {
								showLinkedNotesModal = false;
							}}
						>
							<ChevronLeft className="size-4 rotate-180" strokeWidth="2.5" />
						</button>
					</div>

					<div
						class="mt-4 flex items-center gap-2 rounded-2xl border border-[rgba(231,225,216,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 text-[var(--dochat-text-soft)]"
					>
						<Search className="size-4" />
						<input
							class="w-full bg-transparent outline-none text-sm text-[var(--dochat-text)]"
							bind:value={linkedNoteQuery}
							placeholder="Buscar notas por titulo ou conteudo"
							aria-label="Buscar notas"
						/>
					</div>

					<div class="mt-4 max-h-[26rem] overflow-auto space-y-2">
						{#if linkedNotesLoading}
							<div class="py-8 flex justify-center"><Spinner className="size-4" /></div>
						{:else if linkedNoteCandidates.length === 0}
							<div class="py-8 text-center text-sm text-[var(--dochat-text-soft)]">
								Nenhuma nota encontrada.
							</div>
						{:else}
							{#each linkedNoteCandidates as note}
								<div
									class="flex items-center justify-between gap-3 rounded-2xl border border-[rgba(231,225,216,0.94)] bg-[rgba(251,249,245,0.9)] px-4 py-3"
								>
									<div class="min-w-0">
										<div class="truncate text-sm font-semibold text-[var(--dochat-text)]">
											{note.title || 'Nota sem titulo'}
										</div>
										<div class="mt-1 line-clamp-2 text-xs text-[var(--dochat-text-soft)]">
											{note?.data?.content?.md || 'Sem conteudo.'}
										</div>
									</div>
									<button
										type="button"
										class="shrink-0 rounded-full bg-[var(--dochat-accent-soft)] px-3 py-2 text-xs font-semibold text-[var(--dochat-accent)] hover:bg-[rgba(111,138,100,0.16)]"
										on:click={() => addLinkedNoteHandler(note)}
									>
										Adicionar
									</button>
								</div>
							{/each}
						{/if}
					</div>
				</div>
			</div>
		{/if}

		{#if showDocumentMetadataModal && selectedFile}
			<div
				class="fixed inset-0 z-[1001] flex items-center justify-center p-4 bg-black/15 backdrop-blur-sm"
			>
				<div
					class="w-full max-w-3xl max-h-[82vh] overflow-y-auto rounded-[1.5rem] border border-[rgba(221,214,202,0.92)] bg-white p-4 shadow-[0_24px_64px_rgba(84,74,58,0.12)]"
				>
					<div class="flex items-start justify-between gap-4">
						<div>
							<div
								class="text-[0.72rem] font-bold uppercase tracking-[0.08em] text-[var(--dochat-accent)]"
							>
								Metadados com IA
							</div>
							<div class="mt-1 text-lg font-semibold text-[var(--dochat-text)]">
								{selectedFile?.meta?.name || 'Documento'}
							</div>
						</div>

						<button
							type="button"
							class="rounded-full p-2 text-[var(--dochat-text-faint)] hover:bg-[var(--dochat-bg)] hover:text-[var(--dochat-accent)]"
							aria-label={$i18n.t('Close')}
							on:click={() => {
								showDocumentMetadataModal = false;
							}}
						>
							<ChevronLeft className="size-4 rotate-180" strokeWidth="2.5" />
						</button>
					</div>

					<label class="mt-4 flex flex-col gap-2">
						<span
							class="text-xs font-semibold uppercase tracking-[0.06em] text-[var(--dochat-text-soft)]"
						>
							Instrucao para a IA
						</span>
						<textarea
							class="min-h-24 rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
							bind:value={documentMetadataInstruction}
							placeholder="Ex.: Extraia taxonomia de pesquisa, entidades e descricao executiva."
							disabled={!canEditSelectedFileMetadata}
						></textarea>
					</label>

					<div class="mt-4 flex flex-wrap gap-2">
						<button
							type="button"
							class="rounded-full bg-[var(--dochat-accent-soft)] px-4 py-2 text-sm font-semibold text-[var(--dochat-accent)] hover:bg-[rgba(111,138,100,0.16)]"
							on:click={generateDocumentMetadataHandler}
							disabled={documentMetadataLoading || !canEditSelectedFileMetadata}
						>
							{documentMetadataLoading ? 'Gerando...' : 'Gerar metadados com IA'}
						</button>
						<button
							type="button"
							class="rounded-full bg-[var(--dochat-accent)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--dochat-accent-hover)]"
							on:click={saveDocumentMetadataHandler}
							disabled={documentMetadataSaving || !canEditSelectedFileMetadata}
						>
							{documentMetadataSaving ? 'Salvando...' : 'Salvar'}
						</button>
						{#if isFileLockedByStatus(selectedFile)}
							<button
								type="button"
								class="rounded-full border border-[rgba(179,90,90,0.22)] bg-[rgba(255,245,245,0.9)] px-4 py-2 text-sm font-semibold text-[#9a4d4d] hover:bg-[rgba(255,240,240,0.95)] disabled:opacity-50"
								on:click={createEditableCopyHandler}
								disabled={createEditableCopyLoading}
							>
								{createEditableCopyLoading ? 'Criando copia...' : 'Criar copia editavel'}
							</button>
						{/if}
					</div>

					<div class="mt-4 grid grid-cols-2 gap-3">
						{#if isFileLockedByStatus(selectedFile)}
							<div
								class="col-span-2 rounded-2xl border border-[rgba(179,90,90,0.2)] bg-[rgba(255,245,245,0.9)] px-4 py-3 text-sm text-[#8b4a4a]"
							>
								Este documento esta concluido e bloqueado para edicao direta. Apenas a criacao de
								copia editavel permanece disponivel.
							</div>
						{/if}
						<label class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Titulo</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={documentMetadataDraft.title}
								disabled={!canEditSelectedFileMetadata}
							/>
						</label>
						<label class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Descricao</span>
							<textarea
								class="min-h-24 rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={documentMetadataDraft.description}
								disabled={!canEditSelectedFileMetadata}
							></textarea>
						</label>
						<label class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Resumo</span>
							<textarea
								class="min-h-24 rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={documentMetadataDraft.summary}
								disabled={!canEditSelectedFileMetadata}
							></textarea>
						</label>
						<label class="flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Autor</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={documentMetadataDraft.author}
								disabled={!canEditSelectedFileMetadata}
							/>
						</label>
						<label class="flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Origem</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={documentMetadataDraft.source}
								disabled={!canEditSelectedFileMetadata}
							/>
						</label>
						<label class="flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Idioma</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={documentMetadataDraft.language}
								disabled={!canEditSelectedFileMetadata}
							/>
						</label>
						<label class="flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Tipologia</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={documentMetadataDraft.document_type}
								disabled={!canEditSelectedFileMetadata}
							/>
						</label>
						<label class="flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Status</span>
							<select
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={documentMetadataDraft.document_status}
								disabled={!canEditSelectedFileMetadata}
							>
								<option value="Em elaboração">Em elaboração</option>
								<option value="Em revisão">Em revisão</option>
								<option value="Concluído">Concluído</option>
								<option value="Finalizado">Finalizado</option>
							</select>
						</label>
						<label class="flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Versao</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={documentMetadataDraft.version}
								disabled={!canEditSelectedFileMetadata}
							/>
						</label>
						<label class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Tags</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={documentMetadataDraft.tags}
								disabled={!canEditSelectedFileMetadata}
							/>
						</label>
						<label class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Entidades</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={documentMetadataDraft.entities}
								disabled={!canEditSelectedFileMetadata}
							/>
						</label>
						<label class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]"
								>Pistas de colecao</span
							>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={documentMetadataDraft.suggested_collection_hints}
								disabled={!canEditSelectedFileMetadata}
							/>
						</label>
						<label class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Pasta</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={documentMetadataDraft.folder_path}
								placeholder="Ex.: Contratos/2026"
								disabled={!canEditSelectedFileMetadata}
							/>
						</label>
					</div>
				</div>
			</div>
		{/if}

		{#if showMoveDocumentModal && selectedFile}
			<div
				class="fixed inset-0 z-[1001] flex items-center justify-center p-4 bg-black/15 backdrop-blur-sm"
			>
				<div
					class="w-full max-w-5xl rounded-[1.5rem] border border-[rgba(221,214,202,0.92)] bg-white p-4 shadow-[0_24px_64px_rgba(84,74,58,0.12)]"
				>
					<div class="flex items-start justify-between gap-4">
						<div>
							<div
								class="text-[0.72rem] font-bold uppercase tracking-[0.08em] text-[var(--dochat-accent)]"
							>
								Mover documento
							</div>
							<div class="mt-1 text-lg font-semibold text-[var(--dochat-text)]">
								{getFileTitle(selectedFile)}
							</div>
							<div class="mt-1 text-sm text-[var(--dochat-text-soft)]">
								Local atual: {getFileFolder(selectedFile) || 'Raiz'}
							</div>
						</div>

						<button
							type="button"
							class="rounded-full p-2 text-[var(--dochat-text-faint)] hover:bg-[var(--dochat-bg)] hover:text-[var(--dochat-accent)]"
							aria-label={$i18n.t('Close')}
							on:click={() => {
								showMoveDocumentModal = false;
							}}
						>
							<ChevronLeft className="size-4 rotate-180" strokeWidth="2.5" />
						</button>
					</div>

					<div class="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
						<div
							class="rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.72)] p-4"
						>
							<div
								class="text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[var(--dochat-accent)]"
							>
								Explorador de pastas
							</div>
							<div class="mt-3 flex flex-wrap items-center gap-2 text-sm">
								<button
									type="button"
									class:selected-folder={moveDocumentBrowsePath === ''}
									class="rounded-full bg-[var(--dochat-accent-soft)] px-3 py-1.5 font-semibold text-[var(--dochat-accent)]"
									on:click={() => {
										moveDocumentBrowsePath = '';
										moveDocumentTargetPath = '';
									}}
								>
									Raiz
								</button>
								{#each getFolderAncestors(moveDocumentBrowsePath) as crumb}
									<span class="text-[var(--dochat-text-faint)]">/</span>
									<button
										type="button"
										class="rounded-full border border-[rgba(231,225,216,0.92)] px-3 py-1.5 font-medium text-[var(--dochat-text-soft)] hover:border-[rgba(111,138,100,0.24)] hover:text-[var(--dochat-accent)]"
										on:click={() => {
											moveDocumentBrowsePath = crumb.path;
											moveDocumentTargetPath = crumb.path;
										}}
									>
										{crumb.label}
									</button>
								{/each}
							</div>

							<div class="mt-4 grid gap-2 sm:grid-cols-2">
								{#each getFolderChildren(moveDocumentBrowsePath) as folder}
									<button
										type="button"
										class="flex items-center justify-between rounded-3xl border border-[rgba(231,225,216,0.92)] bg-white px-4 py-3 text-left hover:border-[rgba(111,138,100,0.24)] hover:bg-[rgba(232,239,228,0.24)]"
										on:click={() => {
											moveDocumentBrowsePath = folder;
											moveDocumentTargetPath = folder;
										}}
									>
										<div class="min-w-0">
											<div
												class="flex items-center gap-2 text-sm font-semibold text-[var(--dochat-text)]"
											>
												<Folder className="size-4 shrink-0" />
												<span class="truncate">{getFolderLabel(folder)}</span>
											</div>
											<div class="mt-1 text-xs text-[var(--dochat-text-soft)]">
												{getFolderDocumentCount(folder)} documento(s)
											</div>
										</div>
										<ChevronLeft className="size-4 rotate-180 text-[var(--dochat-text-faint)]" />
									</button>
								{/each}
							</div>

							{#if getFolderChildren(moveDocumentBrowsePath).length === 0}
								<div
									class="mt-4 rounded-3xl border border-dashed border-[rgba(221,214,202,0.96)] bg-white/70 px-4 py-6 text-center text-sm text-[var(--dochat-text-soft)]"
								>
									Esta pasta nao tem subpastas. Pode mover o documento para aqui ou criar uma nova.
								</div>
							{/if}

							<div class="mt-4 rounded-3xl border border-[rgba(231,225,216,0.92)] bg-white p-4">
								<div
									class="text-xs font-semibold uppercase tracking-[0.06em] text-[var(--dochat-text-soft)]"
								>
									Nova pasta nesta localizacao
								</div>
								<div class="mt-3 flex flex-wrap gap-2">
									<input
										class="min-w-0 flex-1 rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
										bind:value={moveDocumentNewFolderName}
										placeholder={moveDocumentBrowsePath
											? `Ex.: ${moveDocumentBrowsePath}/Aprovados`
											: 'Ex.: Contratos/2026'}
									/>
									<button
										type="button"
										class="rounded-full bg-[var(--dochat-accent-soft)] px-4 py-2 text-sm font-semibold text-[var(--dochat-accent)] hover:bg-[rgba(111,138,100,0.16)]"
										on:click={createMoveTargetFolderHandler}
									>
										Criar pasta
									</button>
								</div>
							</div>
						</div>

						<div
							class="rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.72)] p-4"
						>
							<div
								class="text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[var(--dochat-accent)]"
							>
								Destino
							</div>
							<div
								class="mt-3 rounded-3xl border border-[rgba(231,225,216,0.92)] bg-white px-4 py-4"
							>
								<div class="text-xs text-[var(--dochat-text-soft)]">Pasta selecionada</div>
								<div class="mt-2 text-sm font-semibold text-[var(--dochat-text)]">
									{moveDocumentTargetPath || 'Raiz'}
								</div>
							</div>

							<div
								class="mt-4 rounded-3xl border border-[rgba(231,225,216,0.92)] bg-white px-4 py-4"
							>
								<div class="text-xs text-[var(--dochat-text-soft)]">Documento</div>
								<div class="mt-2 text-sm font-semibold text-[var(--dochat-text)]">
									{getFileTitle(selectedFile)}
								</div>
								<div class="mt-1 text-xs text-[var(--dochat-text-soft)]">
									De {getFileFolder(selectedFile) || 'Raiz'} para {moveDocumentTargetPath || 'Raiz'}
								</div>
							</div>

							<div class="mt-4 flex flex-col gap-2">
								<button
									type="button"
									class="rounded-full bg-[var(--dochat-accent)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--dochat-accent-hover)] disabled:opacity-50 disabled:cursor-not-allowed"
									on:click={moveSelectedFileHandler}
									disabled={moveDocumentLoading}
								>
									{moveDocumentLoading ? 'A mover...' : 'Mover documento'}
								</button>
								<button
									type="button"
									class="rounded-full border border-[rgba(231,225,216,0.92)] px-4 py-2 text-sm font-semibold text-[var(--dochat-text-soft)] hover:border-[rgba(111,138,100,0.24)] hover:text-[var(--dochat-accent)]"
									on:click={() => {
										showMoveDocumentModal = false;
									}}
								>
									Cancelar
								</button>
							</div>
						</div>
					</div>
				</div>
			</div>
		{/if}

		{#if showCollectionMetadataModal}
			<div
				class="fixed inset-0 z-40 flex items-center justify-center p-4 bg-black/15 backdrop-blur-sm"
			>
				<div
					class="w-full max-w-4xl rounded-[1.5rem] border border-[rgba(221,214,202,0.92)] bg-white p-4 shadow-[0_24px_64px_rgba(84,74,58,0.12)]"
				>
					<div class="flex items-start justify-between gap-4">
						<div>
							<div
								class="text-[0.72rem] font-bold uppercase tracking-[0.08em] text-[var(--dochat-accent)]"
							>
								Metadados em lote
							</div>
							<div class="mt-1 text-lg font-semibold text-[var(--dochat-text)]">
								{knowledge?.name || 'Colecao'}
							</div>
							<div class="mt-1 text-sm text-[var(--dochat-text-soft)]">
								Aplique campos partilhados ou gere metadados com IA para todos os documentos da
								colecao.
							</div>
						</div>

						<button
							type="button"
							class="rounded-full p-2 text-[var(--dochat-text-faint)] hover:bg-[var(--dochat-bg)] hover:text-[var(--dochat-accent)]"
							aria-label={$i18n.t('Close')}
							on:click={() => {
								showCollectionMetadataModal = false;
							}}
						>
							<ChevronLeft className="size-4 rotate-180" strokeWidth="2.5" />
						</button>
					</div>

					<label class="mt-4 flex flex-col gap-2">
						<span
							class="text-xs font-semibold uppercase tracking-[0.06em] text-[var(--dochat-text-soft)]"
						>
							Instrucao para a IA
						</span>
						<textarea
							class="min-h-24 rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
							bind:value={collectionMetadataInstruction}
							placeholder="Ex.: Gere titulos mais claros, resumo executivo e entidades juridicas."
						></textarea>
					</label>

					<div class="mt-4 flex flex-wrap gap-2">
						<button
							type="button"
							class="rounded-full bg-[var(--dochat-accent-soft)] px-4 py-2 text-sm font-semibold text-[var(--dochat-accent)] hover:bg-[rgba(111,138,100,0.16)]"
							on:click={() => applyMetadataToCollection(true)}
							disabled={collectionMetadataLoading || collectionMetadataSaving}
						>
							{collectionMetadataLoading ? 'Gerando...' : 'Gerar com IA para todos'}
						</button>
						<button
							type="button"
							class="rounded-full bg-[var(--dochat-accent)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--dochat-accent-hover)]"
							on:click={() => applyMetadataToCollection(false)}
							disabled={collectionMetadataLoading || collectionMetadataSaving}
						>
							{collectionMetadataSaving ? 'Aplicando...' : 'Aplicar campos partilhados'}
						</button>
					</div>

					<div class="mt-4 grid grid-cols-2 gap-3">
						<label class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Descricao</span>
							<textarea
								class="min-h-24 rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={collectionMetadataDraft.description}
							></textarea>
						</label>
						<label class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Resumo</span>
							<textarea
								class="min-h-24 rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={collectionMetadataDraft.summary}
							></textarea>
						</label>
						<label class="flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Autor</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={collectionMetadataDraft.author}
							/>
						</label>
						<label class="flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Origem</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={collectionMetadataDraft.source}
							/>
						</label>
						<label class="flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Idioma</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={collectionMetadataDraft.language}
							/>
						</label>
						<label class="flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Tipo</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={collectionMetadataDraft.document_type}
							/>
						</label>
						<label class="flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Versao</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={collectionMetadataDraft.version}
							/>
						</label>
						<label class="flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Pasta</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={collectionMetadataDraft.folder_path}
								placeholder="Ex.: Contratos/2026"
							/>
						</label>
						<label class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Tags</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={collectionMetadataDraft.tags}
							/>
						</label>
						<label class="col-span-2 flex flex-col gap-1.5">
							<span class="text-xs font-semibold text-[var(--dochat-text-soft)]">Entidades</span>
							<input
								class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-[rgba(251,249,245,0.94)] px-4 py-3 outline-none"
								bind:value={collectionMetadataDraft.entities}
							/>
						</label>
					</div>
				</div>
			</div>
		{/if}
		<div class="w-full px-2">
			<div class=" flex w-full">
				<div class="flex-1">
					<div class="flex items-center justify-between w-full">
						<div class="w-full flex justify-between items-center">
							<input
								type="text"
								class="text-left w-full text-lg bg-transparent outline-hidden flex-1"
								bind:value={knowledge.name}
								aria-label={$i18n.t('Knowledge Name')}
								placeholder={$i18n.t('Knowledge Name')}
								disabled={!knowledge?.write_access}
								on:input={() => {
									changeDebounceHandler();
								}}
							/>

							<div class="shrink-0 mr-2.5">
								{#if knowledge?.file_count !== undefined}
									<div class="text-xs text-[var(--dochat-accent)] font-semibold">
										{$i18n.t('{{COUNT}} files', {
											COUNT: knowledge?.file_count ?? 0
										})}
									</div>
								{/if}
							</div>
						</div>

						{#if knowledge?.write_access}
							<div class="self-center shrink-0 flex items-center gap-2">
								<button
									class="bg-[var(--dochat-accent-soft)] hover:bg-[rgba(111,138,100,0.16)] text-[var(--dochat-accent)] transition px-3 py-1.5 rounded-full flex gap-1.5 items-center text-sm font-medium"
									type="button"
									on:click={openChatWithCollection}
								>
									<span>Abrir chat com esta colecao</span>
								</button>
								<button
									class="bg-[var(--dochat-accent)] hover:bg-[var(--dochat-accent-hover)] text-white transition px-3 py-1.5 rounded-full flex gap-1.5 items-center text-sm font-medium"
									type="button"
									on:click={openCollectionMetadataModal}
								>
									<Sparkles className="size-4" />
									<span>Metadados para a colecao</span>
								</button>
								<button
									class="bg-[rgba(232,239,228,0.72)] hover:bg-[rgba(232,239,228,0.96)] text-[var(--dochat-accent)] transition px-2 py-1 rounded-full flex gap-1 items-center"
									type="button"
									on:click={() => {
										showAccessControlModal = true;
									}}
								>
									<LockClosed strokeWidth="2.5" className="size-3.5" />

									<div class="text-sm font-medium shrink-0">
										{$i18n.t('Access')}
									</div>
								</button>
							</div>
						{:else}
							<div class="text-xs shrink-0 text-gray-500">
								{$i18n.t('Read Only')}
							</div>
						{/if}
					</div>

					<div class="flex w-full">
						<input
							type="text"
							class="text-left text-xs w-full text-gray-500 bg-transparent outline-hidden"
							bind:value={knowledge.description}
							aria-label={$i18n.t('Knowledge Description')}
							placeholder={$i18n.t('Knowledge Description')}
							disabled={!knowledge?.write_access}
							on:input={() => {
								changeDebounceHandler();
							}}
						/>
					</div>

					<div class="mt-3 flex flex-col gap-2">
						<div class="flex items-center justify-between gap-3">
							<div
								class="text-xs font-semibold uppercase tracking-[0.08em] text-[var(--dochat-accent)]"
							>
								Notas vinculadas
							</div>
							{#if knowledge?.write_access}
								<button
									type="button"
									class="rounded-full bg-[var(--dochat-accent-soft)] px-3 py-1.5 text-xs font-semibold text-[var(--dochat-accent)] hover:bg-[rgba(111,138,100,0.16)]"
									on:click={() => {
										showLinkedNotesModal = true;
										loadLinkedNoteCandidates();
									}}
								>
									Adicionar nota
								</button>
							{/if}
						</div>

						{#if (knowledge?.linked_notes?.length ?? 0) > 0}
							<div class="flex flex-wrap gap-2">
								{#each knowledge?.linked_notes ?? [] as note}
									<div
										class="flex items-center gap-2 rounded-full border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.92)] px-3 py-1.5 text-xs text-[var(--dochat-text-soft)]"
									>
										<button
											type="button"
											class="truncate font-medium text-[var(--dochat-text)] hover:text-[var(--dochat-accent)]"
											on:click={() => goto(`/notes/${note.id}`)}
										>
											{note.title || 'Nota sem titulo'}
										</button>
										{#if knowledge?.write_access}
											<button
												type="button"
												class="text-[var(--dochat-text-faint)] hover:text-[#b35a5a]"
												aria-label="Remover nota vinculada"
												on:click={() => removeLinkedNoteHandler(note.id)}
											>
												<XMark className="size-3.5" strokeWidth="2" />
											</button>
										{/if}
									</div>
								{/each}
							</div>
						{:else}
							<div class="text-xs text-[var(--dochat-text-faint)]">
								Nenhuma nota vinculada ainda.
							</div>
						{/if}
					</div>

					<div
						class="mt-4 rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.82)] px-4 py-4"
					>
						<div class="flex flex-wrap items-start justify-between gap-3">
							<div class="min-w-0">
								<div
									class="text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[var(--dochat-accent)]"
								>
									Sintese
								</div>
								<div class="mt-1 text-sm font-semibold text-[var(--dochat-text)]">
									Resumo incremental da colecao em nota Markdown
								</div>
								<div class="mt-1 text-xs text-[var(--dochat-text-soft)]">
									Processa os documentos em etapas pequenas, consolida por documento e gera uma
									visao transversal final da colecao.
								</div>
							</div>

							<div class="flex flex-wrap items-center gap-2">
								{#if knowledge?.write_access}
									<button
										type="button"
										class="rounded-full bg-[var(--dochat-accent)] px-4 py-2 text-sm font-semibold text-white hover:bg-[var(--dochat-accent-hover)] disabled:cursor-not-allowed disabled:opacity-60"
										on:click={startCollectionSynthesisHandler}
										disabled={synthesisActionLoading || isSynthesisRunning(synthesisReport?.status)}
									>
										{#if synthesisActionLoading}
											A preparar...
										{:else}
											Gerar Sintese
										{/if}
									</button>

									{#if synthesisReport?.status && synthesisReport.status !== 'idle'}
										<button
											type="button"
											class="rounded-full bg-[var(--dochat-accent-soft)] px-4 py-2 text-sm font-semibold text-[var(--dochat-accent)] hover:bg-[rgba(111,138,100,0.16)] disabled:cursor-not-allowed disabled:opacity-60"
											on:click={regenerateCollectionSynthesisHandler}
											disabled={synthesisActionLoading ||
												isSynthesisRunning(synthesisReport?.status)}
										>
											Regenerar do zero
										</button>
									{/if}
								{/if}

								<button
									type="button"
									class="inline-flex items-center gap-2 rounded-full border border-[rgba(231,225,216,0.92)] bg-white/70 px-3.5 py-2 text-sm font-semibold text-[var(--dochat-text)] hover:border-[rgba(111,138,100,0.24)] hover:bg-[rgba(232,239,228,0.24)]"
									on:click={toggleSynthesisPanel}
									aria-expanded={!synthesisCollapsed}
									aria-label={synthesisCollapsed ? 'Expandir sintese' : 'Retrair sintese'}
								>
									<span>{synthesisCollapsed ? 'Expandir' : 'Retrair'}</span>
									<ChevronLeft
										className={`size-4 text-[var(--dochat-text-faint)] ${synthesisCollapsed ? '-rotate-90' : 'rotate-90'}`}
										strokeWidth="2.2"
									/>
								</button>
							</div>
						</div>

						{#if !synthesisCollapsed}
							{#if synthesisLoading && !synthesisReport}
								<div class="mt-4 flex items-center gap-2 text-sm text-[var(--dochat-text-soft)]">
									<Spinner className="size-4" />
									<span>A carregar o estado da sintese...</span>
								</div>
							{:else if synthesisReport}
								<div class="mt-4 grid gap-3 md:grid-cols-3">
									<div
										class="rounded-2xl border border-[rgba(231,225,216,0.82)] bg-white/70 px-3.5 py-3"
									>
										<div
											class="text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[var(--dochat-text-faint)]"
										>
											Estado
										</div>
										<div
											class={`mt-2 inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ${getSynthesisStatusTone(synthesisReport.status)}`}
										>
											{getSynthesisStatusLabel(synthesisReport.status)}
										</div>
										{#if synthesisReport.currentStep}
											<div class="mt-2 text-xs text-[var(--dochat-text-soft)]">
												{getSynthesisStepLabel(synthesisReport.currentStep)}
											</div>
										{/if}
										{#if synthesisReport.currentDocumentTitle}
											<div class="mt-1 text-xs text-[var(--dochat-text-soft)]">
												Agora: {synthesisReport.currentDocumentTitle}
											</div>
										{/if}
									</div>

									<div
										class="rounded-2xl border border-[rgba(231,225,216,0.82)] bg-white/70 px-3.5 py-3"
									>
										<div
											class="text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[var(--dochat-text-faint)]"
										>
											Progresso
										</div>
										<div class="mt-2 text-lg font-semibold text-[var(--dochat-text)]">
											{synthesisReport.documentsCompleted ?? 0}/{synthesisReport.documentsTotal ??
												0}
										</div>
										<div class="mt-1 text-xs text-[var(--dochat-text-soft)]">
											Concluidos: {synthesisReport.documentsCompleted ?? 0} • Falhas:
											{synthesisReport.documentsFailed ?? 0}
										</div>
									</div>

									<div
										class="rounded-2xl border border-[rgba(231,225,216,0.82)] bg-white/70 px-3.5 py-3"
									>
										<div
											class="text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[var(--dochat-text-faint)]"
										>
											Artefato final
										</div>
										<div class="mt-2 text-sm font-semibold text-[var(--dochat-text)]">
											{synthesisReport.noteTitle || 'Nota ainda nao criada'}
										</div>
										<div class="mt-1 text-xs text-[var(--dochat-text-soft)]">
											{formatSynthesisTimestamp(synthesisReport.metadata?.generatedAt)}
										</div>
										{#if synthesisReport.noteId}
											<button
												type="button"
												class="mt-3 rounded-full bg-[var(--dochat-accent-soft)] px-3 py-1.5 text-xs font-semibold text-[var(--dochat-accent)] hover:bg-[rgba(111,138,100,0.16)]"
												on:click={() => goto(`/notes/${synthesisReport?.noteId}`)}
											>
												Abrir nota
											</button>
										{/if}
									</div>
								</div>

								{#if synthesisReport.error}
									<div
										class="mt-4 rounded-2xl border border-[rgba(220,173,173,0.66)] bg-[rgba(253,244,244,0.84)] px-4 py-3 text-sm text-[#9d5151]"
									>
										{synthesisReport.error}
									</div>
								{/if}

								{#if (synthesisReport.warnings?.length ?? 0) > 0}
									<div class="mt-4">
										<div
											class="text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[#9b6a1d]"
										>
											Avisos
										</div>
										<div class="mt-2 flex flex-col gap-2">
											{#each synthesisReport.warnings ?? [] as warning}
												<div
													class="rounded-2xl border border-[rgba(237,214,170,0.42)] bg-[rgba(255,248,232,0.88)] px-3 py-2 text-xs text-[#7a5b1d]"
												>
													{warning}
												</div>
											{/each}
										</div>
									</div>
								{/if}

								{#if (synthesisReport.documentStatuses?.length ?? 0) > 0}
									<div class="mt-4">
										<div
											class="text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[var(--dochat-text-faint)]"
										>
											Status por documento
										</div>
										<div class="mt-2 flex flex-col gap-2">
											{#each synthesisReport.documentStatuses ?? [] as item}
												<div
													class="rounded-2xl border border-[rgba(231,225,216,0.82)] bg-white/70 px-3.5 py-3"
												>
													<div class="flex flex-wrap items-start justify-between gap-2">
														<div class="min-w-0">
															<div class="text-sm font-semibold text-[var(--dochat-text)]">
																{item.title || 'Documento'}
															</div>
															<div class="mt-1 text-xs text-[var(--dochat-text-soft)]">
																{item.sourceType || 'documento'}
																{#if item.pageCount}
																	• {item.pageCount} p.
																{/if}
															</div>
														</div>
														<div
															class={`inline-flex rounded-full px-2.5 py-1 text-[0.7rem] font-semibold ${getSynthesisStatusTone(item.status)}`}
														>
															{getSynthesisStatusLabel(item.status)}
														</div>
													</div>

													{#if item.error}
														<div class="mt-2 text-xs text-[#9d5151]">{item.error}</div>
													{/if}

													{#if (item.warnings?.length ?? 0) > 0}
														<div class="mt-2 flex flex-col gap-1.5">
															{#each item.warnings ?? [] as warning}
																<div class="text-xs text-[#7a5b1d]">{warning}</div>
															{/each}
														</div>
													{/if}
												</div>
											{/each}
										</div>
									</div>
								{/if}

								{#if synthesisReport.report}
									<div
										class="mt-4 rounded-2xl border border-[rgba(231,225,216,0.82)] bg-white/70 px-4 py-4"
									>
										<div
											class="text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[var(--dochat-accent)]"
										>
											Leitura rapida
										</div>

										{#if synthesisReport.report?.overview}
											<div class="mt-3">
												<div class="text-sm font-semibold text-[var(--dochat-text)]">
													Visao geral
												</div>
												<p class="mt-1 text-sm leading-6 text-[var(--dochat-text-soft)]">
													{synthesisReport.report.overview}
												</p>
											</div>
										{/if}

										{#each [{ title: 'Temas recorrentes', items: synthesisReport.report?.recurringThemes ?? [] }, { title: 'Principais achados', items: synthesisReport.report?.mainFindings ?? [] }, { title: 'Convergencias', items: synthesisReport.report?.convergences ?? [] }, { title: 'Divergencias', items: synthesisReport.report?.divergences ?? [] }, { title: 'Lacunas', items: synthesisReport.report?.gaps ?? [] }, { title: 'Perguntas futuras', items: synthesisReport.report?.futureQuestions ?? [] }] as section}
											{#if section.items.length > 0}
												<div class="mt-4">
													<div class="text-sm font-semibold text-[var(--dochat-text)]">
														{section.title}
													</div>
													<ul
														class="mt-2 flex flex-col gap-2 text-sm text-[var(--dochat-text-soft)]"
													>
														{#each section.items as item}
															<li class="rounded-2xl bg-[rgba(246,243,237,0.9)] px-3 py-2">
																{item}
															</li>
														{/each}
													</ul>
												</div>
											{/if}
										{/each}

										{#if synthesisReport.report?.conclusion}
											<div class="mt-4">
												<div class="text-sm font-semibold text-[var(--dochat-text)]">Conclusao</div>
												<p class="mt-1 text-sm leading-6 text-[var(--dochat-text-soft)]">
													{synthesisReport.report.conclusion}
												</p>
											</div>
										{/if}
									</div>
								{/if}
							{:else}
								<div class="mt-4 text-sm text-[var(--dochat-text-soft)]">
									Quando executada, a sintese vai resumir cada documento em etapas pequenas e criar
									uma nota final ligada a esta colecao.
								</div>
							{/if}
						{/if}
					</div>

					{#if selectedFile?.id}
						<div
							class="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.82)] px-4 py-3"
						>
							<div class="min-w-0">
								<div
									class="text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[var(--dochat-accent)]"
								>
									Documento selecionado
								</div>
								<div class="mt-1 truncate text-sm font-semibold text-[var(--dochat-text)]">
									{getFileTitle(selectedFile)}
								</div>
								<div
									class="mt-1 flex flex-wrap items-center gap-2 text-xs text-[var(--dochat-text-soft)]"
								>
									<span>Localizacao: {getFileFolder(selectedFile) || 'Raiz'}</span>
									<span
										class="rounded-full bg-[var(--dochat-accent-soft)] px-2 py-0.5 font-semibold text-[var(--dochat-accent)]"
									>
										{getFileDocumentType(selectedFile)}
									</span>
									<span
										class="rounded-full bg-[rgba(20,20,20,0.05)] px-2 py-0.5 font-semibold text-[var(--dochat-text-soft)]"
									>
										{getFileDocumentStatus(selectedFile)}
									</span>
									{#if isFileLockedByStatus(selectedFile)}
										<span
											class="rounded-full bg-[rgba(179,90,90,0.12)] px-2 py-0.5 font-semibold text-[#9a4d4d]"
										>
											Travado para edicao
										</span>
									{/if}
								</div>
							</div>

							{#if knowledge?.write_access}
								<div class="flex flex-wrap items-center gap-2">
									{#if isFileLockedByStatus(selectedFile)}
										<button
											type="button"
											class="rounded-full bg-[var(--dochat-accent)] px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
											on:click={createEditableCopyHandler}
											disabled={createEditableCopyLoading}
										>
											{createEditableCopyLoading ? 'Criando copia...' : 'Criar copia editavel'}
										</button>
									{/if}

									<button
										type="button"
										class="rounded-full bg-[var(--dochat-accent-soft)] px-4 py-2 text-sm font-semibold text-[var(--dochat-accent)] hover:bg-[rgba(111,138,100,0.16)] disabled:opacity-50 disabled:cursor-not-allowed"
										on:click={() => openMoveDocumentModal(selectedFile)}
										disabled={isFileCheckedOutByOtherUser(selectedFile) ||
											isFileLockedByStatus(selectedFile)}
									>
										Mover entre pastas
									</button>
								</div>
							{/if}
						</div>
					{/if}
				</div>
			</div>
		</div>

		<div
			class="mt-2 mb-2.5 py-2 -mx-0 bg-white dark:bg-gray-900 rounded-3xl border border-gray-100/30 dark:border-gray-850/30 flex-1"
		>
			<div class="px-3.5 flex flex-1 items-center w-full space-x-2 py-0.5 pb-2">
				<div class="flex flex-1 items-center">
					<div class=" self-center ml-1 mr-3">
						<Search className="size-3.5" />
					</div>
					<input
						class=" w-full text-sm pr-4 py-1 rounded-r-xl outline-hidden bg-transparent"
						bind:value={query}
						aria-label={$i18n.t('Search Collection')}
						placeholder={$i18n.t('Search Collection')}
						on:focus={() => {
							selectedFileId = null;
						}}
					/>

					{#if knowledge?.write_access}
						<div>
							<AddContentMenu
								onUpload={handleAddContentUpload}
								onSync={() => {
									showSyncConfirmModal = true;
								}}
							/>
						</div>
					{/if}
				</div>
			</div>

			<div class="px-3 flex justify-between">
				<div
					class="flex w-full bg-transparent overflow-x-auto scrollbar-none"
					on:wheel={(e) => {
						if (e.deltaY !== 0) {
							e.preventDefault();
							e.currentTarget.scrollLeft += e.deltaY;
						}
					}}
				>
					<div
						class="flex gap-3 w-fit text-center text-sm rounded-full bg-transparent px-0.5 whitespace-nowrap"
					>
						<DropdownOptions
							align="start"
							className="flex w-full items-center gap-2 truncate px-3 py-1.5 text-sm bg-gray-50 dark:bg-gray-850 rounded-xl  placeholder-gray-400 outline-hidden focus:outline-hidden"
							bind:value={viewOption}
							items={[
								{ value: '', label: $i18n.t('All') },
								{ value: 'created', label: $i18n.t('Created by you') },
								{ value: 'shared', label: $i18n.t('Shared with you') }
							]}
							onChange={(value) => {
								if (value) {
									localStorage.workspaceViewOption = value;
								} else {
									delete localStorage.workspaceViewOption;
								}
							}}
						/>

						<DropdownOptions
							align="start"
							bind:value={documentTypeFilter}
							placeholder="Tipologia"
							items={[
								{ value: '', label: 'Todas as tipologias' },
								...availableDocumentTypes.map((value) => ({ value, label: value }))
							]}
						/>

						<DropdownOptions
							align="start"
							bind:value={documentStatusFilter}
							placeholder="Status"
							items={[
								{ value: '', label: 'Todos os status' },
								...availableDocumentStatuses.map((value) => ({ value, label: value }))
							]}
						/>

						<DropdownOptions
							align="start"
							bind:value={lockedFilter}
							placeholder="Bloqueio"
							items={[
								{ value: '', label: 'Travados e editaveis' },
								{ value: 'locked', label: 'Somente travados' },
								{ value: 'editable', label: 'Somente editaveis' }
							]}
						/>

						<DropdownOptions
							align="start"
							bind:value={sortKey}
							placeholder={$i18n.t('Sort')}
							items={[
								{ value: 'name', label: $i18n.t('Name') },
								{ value: 'created_at', label: $i18n.t('Created At') },
								{ value: 'updated_at', label: $i18n.t('Updated At') }
							]}
						/>

						{#if sortKey}
							<DropdownOptions
								align="start"
								bind:value={direction}
								items={[
									{ value: 'asc', label: $i18n.t('Asc') },
									{ value: '', label: $i18n.t('Desc') }
								]}
							/>
						{/if}

						<button
							type="button"
							class="rounded-xl border border-[rgba(111,138,100,0.18)] bg-[var(--dochat-accent-soft)] px-3 py-1.5 text-sm font-medium text-[var(--dochat-accent)]"
							on:click={() => {
								collectionDisplayMode = collectionDisplayMode === 'table' ? 'list' : 'table';
							}}
						>
							{collectionDisplayMode === 'table' ? 'Vista em lista' : 'Vista em tabela'}
						</button>

						<button
							type="button"
							class="rounded-xl border border-[rgba(111,138,100,0.18)] bg-[rgba(232,239,228,0.72)] px-3 py-1.5 text-sm font-medium text-[var(--dochat-accent)]"
							on:click={createFolderHandler}
						>
							<NewFolderAlt className="mr-1 inline size-4" />
							Nova pasta
						</button>

						<button
							type="button"
							class="rounded-xl border border-[rgba(111,138,100,0.18)] bg-[var(--dochat-accent)] px-3 py-1.5 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
							on:click={() => openChatWithDocuments(selectedContextDocuments)}
							disabled={selectedContextDocuments.length === 0}
						>
							{selectedContextDocuments.length > 0
								? `Conversar com ${selectedContextDocuments.length} documento(s)`
								: 'Conversar com selecionados'}
						</button>
					</div>
				</div>
			</div>

			<div class="flex flex-row flex-1 gap-3 px-2.5 mt-2 min-h-0">
				<aside
					class="hidden w-64 shrink-0 overflow-y-auto rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.82)] p-3 lg:block"
				>
					<div
						class="text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[var(--dochat-accent)]"
					>
						Explorer
					</div>
					<div class="mt-3 flex flex-col gap-1.5">
						<button
							type="button"
							class:selected-folder={selectedFolder === ''}
							class="flex items-center gap-2 rounded-2xl px-3 py-2 text-left text-sm font-medium text-[var(--dochat-text-soft)]"
							on:click={() => {
								selectedFolder = '';
							}}
						>
							<Folder className="size-4" />
							<span>Raiz</span>
						</button>

						{#each derivedFolders as folder}
							<button
								type="button"
								class:selected-folder={selectedFolder === folder}
								class="flex items-center gap-2 rounded-2xl py-2 pr-3 text-left text-sm font-medium text-[var(--dochat-text-soft)]"
								style={`padding-left: ${0.75 + getFolderDepth(folder) * 0.85}rem;`}
								on:click={() => {
									selectedFolder = folder;
								}}
							>
								<Folder className="size-4 shrink-0" />
								<span class="truncate">{getFolderLabel(folder)}</span>
							</button>
						{/each}
					</div>
				</aside>

				<div class="flex-1 flex min-w-0">
					<div class="flex flex-col w-full rounded-lg h-full min-h-0">
						<div
							class="mb-3 rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.82)] p-4"
						>
							<div
								class="text-[0.68rem] font-semibold uppercase tracking-[0.08em] text-[var(--dochat-accent)]"
							>
								Pasta atual
							</div>
							<div class="mt-2 flex flex-wrap items-center gap-2 text-sm">
								<button
									type="button"
									class="rounded-full bg-[var(--dochat-accent-soft)] px-3 py-1.5 font-semibold text-[var(--dochat-accent)]"
									on:click={() => {
										selectedFolder = '';
									}}
								>
									Raiz
								</button>
								{#each getFolderAncestors(selectedFolder) as crumb}
									<span class="text-[var(--dochat-text-faint)]">/</span>
									<button
										type="button"
										class="rounded-full border border-[rgba(231,225,216,0.92)] px-3 py-1.5 font-medium text-[var(--dochat-text-soft)] hover:border-[rgba(111,138,100,0.24)] hover:text-[var(--dochat-accent)]"
										on:click={() => {
											selectedFolder = crumb.path;
										}}
									>
										{crumb.label}
									</button>
								{/each}
							</div>
							<div class="mt-3 text-xs text-[var(--dochat-text-soft)]">
								{visibleChildFolders.length} subpasta(s) · {visibleFileItems.length} documento(s) nesta
								vista
							</div>
						</div>

						{#if visibleChildFolders.length > 0}
							<div class="mb-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
								{#each visibleChildFolders as folder}
									<button
										type="button"
										class="flex items-center justify-between rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.82)] px-4 py-3 text-left hover:border-[rgba(111,138,100,0.24)] hover:bg-[rgba(232,239,228,0.24)]"
										on:click={() => {
											selectedFolder = folder;
										}}
									>
										<div class="min-w-0">
											<div
												class="flex items-center gap-2 text-sm font-semibold text-[var(--dochat-text)]"
											>
												<Folder className="size-4 shrink-0" />
												<span class="truncate">{getFolderLabel(folder)}</span>
											</div>
											<div class="mt-1 text-xs text-[var(--dochat-text-soft)]">
												{getFolderDocumentCount(folder)} documento(s)
											</div>
										</div>
										<ChevronLeft className="size-4 rotate-180 text-[var(--dochat-text-faint)]" />
									</button>
								{/each}
							</div>
						{/if}

						<div class="w-full h-full flex flex-col min-h-0">
							{#if visibleChildFolders.length > 0 || visibleFileItems.length > 0}
								{#if collectionDisplayMode === 'table'}
									<div class="overflow-y-auto h-full w-full">
										<table
											class="w-full min-w-[1180px] table-fixed border-separate border-spacing-y-1 text-sm"
										>
											<thead>
												<tr
													class="text-left text-[0.72rem] uppercase tracking-[0.08em] text-[var(--dochat-text-faint)]"
												>
													<th class="px-3 py-2">Chat</th>
													<th class="px-3 py-2">Documento</th>
													<th class="px-3 py-2">Pasta</th>
													<th class="px-3 py-2">Tipo</th>
													<th class="px-3 py-2">Autor</th>
													<th class="px-3 py-2">Upload por</th>
													<th class="px-3 py-2">Alterado por</th>
													<th class="px-3 py-2">Origem</th>
													<th class="px-3 py-2">Tags</th>
													<th class="px-3 py-2">Status</th>
													<th class="px-3 py-2">Bloqueio</th>
													<th class="px-3 py-2">Ultima alteracao</th>
												</tr>
											</thead>
											<tbody>
												{#each visibleFileItems as file (file?.id ?? file?.itemId ?? file?.tempId)}
													<!-- svelte-ignore a11y_click_events_have_key_events a11y_no_noninteractive_element_interactions -->
													<tr
														class:selected-table-row={selectedFileId === (file?.id ?? file?.tempId)}
														class="cursor-pointer rounded-2xl bg-[rgba(251,249,245,0.82)] text-[var(--dochat-text-soft)] shadow-[0_6px_24px_rgba(84,74,58,0.04)]"
														on:click={() => {
															selectedFileId = file?.id ?? file?.tempId ?? null;
															fileSelectHandler(file);
														}}
													>
														<td class="rounded-l-2xl px-3 py-3">
															<input
																type="checkbox"
																checked={selectedContextDocumentIds.includes(file?.id ?? '')}
																disabled={!file?.id}
																aria-label={`Selecionar ${getFileTitle(file)} para conversa`}
																on:change|stopPropagation={() =>
																	toggleContextDocumentSelection(file?.id)}
															/>
														</td>
														<td class="px-3 py-3 font-medium text-[var(--dochat-text)]"
															>{getFileTitle(file)}</td
														>
														<td class="px-3 py-3">{getFileFolder(file) || 'Raiz'}</td>
														<td class="px-3 py-3">{getFileDocumentType(file)}</td>
														<td class="px-3 py-3">{getFileAuthor(file)}</td>
														<td class="px-3 py-3">{getFileUploadActor(file)}</td>
														<td class="px-3 py-3">{getFileModifiedActor(file)}</td>
														<td class="px-3 py-3">{getFileSource(file)}</td>
														<td class="px-3 py-3">{getFileTags(file).join(', ') || '—'}</td>
														<td class="px-3 py-3">
															<span
																class="rounded-full bg-[var(--dochat-accent-soft)] px-2.5 py-1 text-xs font-semibold text-[var(--dochat-accent)]"
															>
																{getFileDocumentStatus(file)}
															</span>
														</td>
														<td class="px-3 py-3">
															<span
																class={`rounded-full px-2.5 py-1 text-xs font-semibold ${
																	isFileLockedByStatus(file)
																		? 'bg-[rgba(179,90,90,0.12)] text-[#9a4d4d]'
																		: 'bg-[rgba(111,138,100,0.12)] text-[var(--dochat-accent)]'
																}`}
															>
																{isFileLockedByStatus(file) ? 'Travado' : getFileStatus(file)}
															</span>
														</td>
														<td class="rounded-r-2xl px-3 py-3">
															{#if getFileLastModifiedAt(file)}
																{dayjs(getFileLastModifiedAt(file) * 1000).fromNow()}
															{:else}
																—
															{/if}
														</td>
													</tr>
												{/each}
											</tbody>
										</table>
									</div>
								{:else}
									<div class=" flex overflow-y-auto h-full w-full scrollbar-hidden text-xs">
										<Files
											files={visibleFileItems}
											{knowledge}
											{selectedFileId}
											selectedDocumentIds={selectedContextDocumentIds}
											onClick={(fileId) => {
												selectedFileId = fileId ?? null;

												if (visibleFileItems) {
													const file = visibleFileItems.find((file) => file.id === selectedFileId);
													if (file) {
														fileSelectHandler(file);
													} else {
														selectedFile = null;
													}
												}
											}}
											onDelete={(fileId) => {
												selectedFileId = null;
												selectedFile = null;

												if (fileId) {
													deleteFileHandler(fileId);
												}
											}}
											onToggleContext={(fileId) => {
												toggleContextDocumentSelection(fileId);
											}}
										/>
									</div>
								{/if}
							{:else}
								<div class="my-3 flex flex-col justify-center text-center text-gray-500 text-xs">
									{#if knowledgeLoading}
										<div class="mb-2 flex justify-center">
											<Spinner className="size-4" />
										</div>
									{/if}
									<div>
										{$i18n.t('No content found')}
									</div>
								</div>
							{/if}
						</div>
					</div>
				</div>

				{#if selectedFileId !== null}
					<Drawer
						className="h-full"
						show={selectedFileId !== null}
						onClose={() => {
							selectedFileId = null;
							selectedFile = null;
							selectedFileTab = 'markdown';
						}}
					>
						<div class="flex flex-col justify-start h-full max-h-full">
							<div class=" flex flex-col w-full h-full max-h-full">
								<div class="shrink-0 flex items-center p-2">
									<div class="mr-2">
										<button
											class="w-full text-left text-sm p-1.5 rounded-lg dark:text-gray-300 dark:hover:text-white hover:bg-black/5 dark:hover:bg-gray-850"
											aria-label={$i18n.t('Close')}
											on:click={() => {
												selectedFileId = null;
												selectedFile = null;
												selectedFileTab = 'markdown';
											}}
										>
											<ChevronLeft strokeWidth="2.5" />
										</button>
									</div>
									<div class=" flex-1 text-lg line-clamp-1">
										{getFileTitle(selectedFile)}
									</div>

									{#if knowledge?.write_access}
										<div class="flex items-center gap-2">
											<button
												class="flex self-center w-fit text-sm py-1.5 px-3 rounded-full bg-[rgba(232,239,228,0.72)] text-[var(--dochat-accent)] hover:bg-[rgba(232,239,228,0.96)] disabled:opacity-50 disabled:cursor-not-allowed"
												type="button"
												on:click={() => openMoveDocumentModal(selectedFile)}
												disabled={isFileCheckedOutByOtherUser(selectedFile)}
											>
												Mover
											</button>
											<button
												class="flex self-center w-fit text-sm py-1.5 px-3 rounded-full bg-[var(--dochat-accent-soft)] text-[var(--dochat-accent)] hover:bg-[rgba(111,138,100,0.16)] disabled:opacity-50 disabled:cursor-not-allowed"
												type="button"
												on:click={openDocumentMetadataModal}
												disabled={isFileCheckedOutByOtherUser(selectedFile)}
											>
												Metadados IA
											</button>
											{#if canEditSelectedFileContent}
												<button
													class="flex self-center w-fit text-sm py-1 px-3 rounded-full bg-[var(--dochat-accent)] text-white hover:bg-[var(--dochat-accent-hover)] disabled:opacity-50 disabled:cursor-not-allowed"
													type="button"
													disabled={isSaving}
													on:click={() => {
														updateFileContentHandler();
													}}
												>
													Check-in e salvar
													{#if isSaving}
														<div class="ml-2 self-center">
															<Spinner />
														</div>
													{/if}
												</button>
											{:else if isFileLockedByStatus(selectedFile)}
												<button
													class="flex self-center w-fit text-sm py-1 px-3 rounded-full bg-[var(--dochat-accent)] text-white hover:bg-[var(--dochat-accent-hover)] disabled:opacity-50 disabled:cursor-not-allowed"
													type="button"
													disabled={createEditableCopyLoading}
													on:click={createEditableCopyHandler}
												>
													Criar copia editavel
													{#if createEditableCopyLoading}
														<div class="ml-2 self-center">
															<Spinner />
														</div>
													{/if}
												</button>
											{:else}
												<button
													class="flex self-center w-fit text-sm py-1 px-3 rounded-full bg-[var(--dochat-accent)] text-white hover:bg-[var(--dochat-accent-hover)] disabled:opacity-50 disabled:cursor-not-allowed"
													type="button"
													disabled={checkoutActionLoading ||
														isFileCheckedOutByOtherUser(selectedFile)}
													on:click={checkoutSelectedFileHandler}
												>
													Check-out
													{#if checkoutActionLoading}
														<div class="ml-2 self-center">
															<Spinner />
														</div>
													{/if}
												</button>
											{/if}
										</div>
									{/if}
								</div>

								<div class="shrink-0 px-3 pb-3">
									<div
										class="grid grid-cols-2 gap-2 rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.9)] p-3 text-xs text-[var(--dochat-text-soft)]"
									>
										<div>
											<div
												class="uppercase tracking-[0.08em] text-[0.65rem] text-[var(--dochat-text-faint)]"
											>
												Upload por
											</div>
											<div class="mt-1 font-semibold text-[var(--dochat-text)]">
												{getFileUploadActor(selectedFile)}
											</div>
										</div>
										<div>
											<div
												class="uppercase tracking-[0.08em] text-[0.65rem] text-[var(--dochat-text-faint)]"
											>
												Alterado por
											</div>
											<div class="mt-1 font-semibold text-[var(--dochat-text)]">
												{getFileModifiedActor(selectedFile)}
											</div>
										</div>
										<div>
											<div
												class="uppercase tracking-[0.08em] text-[0.65rem] text-[var(--dochat-text-faint)]"
											>
												Ultima alteracao
											</div>
											<div class="mt-1 font-semibold text-[var(--dochat-text)]">
												{#if getFileLastModifiedAt(selectedFile)}
													{dayjs(getFileLastModifiedAt(selectedFile) * 1000).format(
														'DD/MM/YYYY HH:mm'
													)}
												{:else}
													—
												{/if}
											</div>
										</div>
										<div>
											<div
												class="uppercase tracking-[0.08em] text-[0.65rem] text-[var(--dochat-text-faint)]"
											>
												Tipologia
											</div>
											<div class="mt-1 font-semibold text-[var(--dochat-text)]">
												{getFileDocumentType(selectedFile)}
											</div>
										</div>
										<div>
											<div
												class="uppercase tracking-[0.08em] text-[0.65rem] text-[var(--dochat-text-faint)]"
											>
												Status documental
											</div>
											<div class="mt-1 font-semibold text-[var(--dochat-text)]">
												{getFileDocumentStatus(selectedFile)}
											</div>
										</div>
										<div>
											<div
												class="uppercase tracking-[0.08em] text-[0.65rem] text-[var(--dochat-text-faint)]"
											>
												Controle de versao
											</div>
											<div class="mt-1 font-semibold text-[var(--dochat-text)]">
												Rev. {getFileVersionControl(selectedFile)?.revision ?? 1}
											</div>
										</div>
										<div class="col-span-2">
											<div
												class="uppercase tracking-[0.08em] text-[0.65rem] text-[var(--dochat-text-faint)]"
											>
												Estado
											</div>
											<div class="mt-1 font-semibold text-[var(--dochat-text)]">
												{#if isFileLockedByStatus(selectedFile)}
													Travado por status terminal
												{:else if getFileVersionControl(selectedFile)?.status === 'checked_out'}
													Em check-out por {selectedFileCheckoutOwnerLabel}
													{#if getFileVersionControl(selectedFile)?.checked_out_at}
														desde {dayjs(
															(getFileVersionControl(selectedFile)?.checked_out_at ?? 0) * 1000
														).format('DD/MM/YYYY HH:mm')}
													{/if}
												{:else}
													Disponivel para check-out
												{/if}
											</div>
											<div class="mt-1 text-[0.7rem] text-[var(--dochat-text-faint)]">
												{#if isFileLockedByStatus(selectedFile)}
													Este documento esta concluido e bloqueado para edicao direta.
												{:else if canEditSelectedFileContent}
													Edite o conteudo e faca check-in para registrar a nova versao.
												{:else if isFileCheckedOutByOtherUser(selectedFile)}
													Este documento esta bloqueado ate o check-in de {selectedFileCheckoutOwnerLabel}.
												{:else if knowledge?.write_access}
													Faca check-out para liberar o editor de conteudo.
												{:else}
													Voce tem apenas permissao de leitura nesta colecao.
												{/if}
											</div>
										</div>
									</div>
								</div>

								<div class="shrink-0 px-3 pb-2">
									<div class="flex flex-wrap items-center gap-2">
										<button
											type="button"
											class:selected-folder={selectedFileTab === 'markdown'}
											class="rounded-full border border-[rgba(231,225,216,0.92)] px-3 py-1.5 text-xs font-semibold text-[var(--dochat-text-soft)]"
											on:click={() => {
												selectedFileTab = 'markdown';
											}}
										>
											Markdown
										</button>
										<button
											type="button"
											class:selected-folder={selectedFileTab === 'original'}
											class="rounded-full border border-[rgba(231,225,216,0.92)] px-3 py-1.5 text-xs font-semibold text-[var(--dochat-text-soft)]"
											on:click={() => {
												selectedFileTab = 'original';
											}}
										>
											Original
										</button>
										<button
											type="button"
											class:selected-folder={selectedFileTab === 'versions'}
											class="rounded-full border border-[rgba(231,225,216,0.92)] px-3 py-1.5 text-xs font-semibold text-[var(--dochat-text-soft)]"
											on:click={() => {
												selectedFileTab = 'versions';
											}}
										>
											Versoes
										</button>
									</div>
								</div>

								<div class="min-h-0 flex-1 px-3 pb-3">
									{#if selectedFileTab === 'markdown'}
										{#key selectedFile?.id ?? 'selected-file'}
											{#if isFileLockedByStatus(selectedFile)}
												<div
													class="mb-3 rounded-2xl border border-[rgba(179,90,90,0.2)] bg-[rgba(255,245,245,0.9)] px-4 py-3 text-sm text-[#8b4a4a]"
												>
													Este documento esta concluido e bloqueado para edicao direta. Crie uma
													copia editavel para continuar o trabalho.
												</div>
											{/if}
											<textarea
												class="w-full h-full text-sm outline-none resize-none rounded-3xl border border-[rgba(231,225,216,0.92)] bg-white px-3 py-3 disabled:bg-[rgba(251,249,245,0.6)] disabled:text-[var(--dochat-text-faint)]"
												bind:value={selectedFileContent}
												disabled={!canEditSelectedFileContent}
												aria-label={$i18n.t('File content')}
												placeholder={canEditSelectedFileContent
													? $i18n.t('Add content here')
													: isFileLockedByStatus(selectedFile)
														? 'Este documento esta travado. Crie uma copia editavel para continuar.'
														: 'Faca check-out para editar este documento.'}
											></textarea>
										{/key}
									{:else if selectedFileTab === 'original'}
										<OriginalDocumentPreview
											token={browser ? localStorage.token : ''}
											file={selectedFile}
											active={selectedFileTab === 'original'}
											initialPage={selectedFileOpenPage}
										/>
									{:else}
										{#key `${selectedFile?.id ?? 'selected-file'}:${getFileVersionControl(selectedFile)?.revision ?? 0}`}
											<DocumentVersions
												token={browser ? localStorage.token : ''}
												file={selectedFile}
												active={selectedFileTab === 'versions'}
												initialTargetRevision={selectedFileOpenRevision}
											/>
										{/key}
									{/if}
								</div>
							</div>
						</div>
					</Drawer>
				{/if}
			</div>
		</div>
	{:else}
		<Spinner className="size-5" />
	{/if}
</div>

<style>
	.selected-folder {
		border-color: rgba(111, 138, 100, 0.24);
		background: var(--dochat-accent-soft, #e8efe4);
		color: var(--dochat-accent, #6f8a64);
	}

	.selected-table-row {
		outline: 1px solid rgba(111, 138, 100, 0.24);
		background: rgba(232, 239, 228, 0.72);
	}
</style>
