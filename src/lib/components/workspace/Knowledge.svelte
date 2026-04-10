<script lang="ts">
	import dayjs from 'dayjs';
	import relativeTime from 'dayjs/plugin/relativeTime';
	dayjs.extend(relativeTime);

	import { toast } from 'svelte-sonner';
	import { onMount, getContext, tick, onDestroy } from 'svelte';
	const i18n = getContext('i18n');

	import { WEBUI_NAME, user } from '$lib/stores';
	import {
		deleteKnowledgeById,
		searchKnowledgeBases,
		exportKnowledgeById,
		resetKnowledgeById
	} from '$lib/apis/knowledge';
	import {
		getDocuments,
		suggestDocumentMetadataById,
		updateDocumentMetadataById
	} from '$lib/apis/documents';

	import { goto } from '$app/navigation';
	import { capitalizeFirstLetter, formatFileSize } from '$lib/utils';

	import DeleteConfirmDialog from '../common/ConfirmDialog.svelte';
	import Badge from '../common/Badge.svelte';
	import Search from '../icons/Search.svelte';
	import Plus from '../icons/Plus.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import XMark from '../icons/XMark.svelte';
	import ViewSelector from './common/ViewSelector.svelte';
	import Loader from '../common/Loader.svelte';
	import FileItemModal from '../common/FileItemModal.svelte';
	import DocumentPage from '../icons/DocumentPage.svelte';

	type DocumentMeta = Record<string, any> & {
		name?: string | null;
		description?: string | null;
		source?: string | null;
		document_type?: string | null;
		document_status?: string | null;
		is_locked_by_status?: boolean | null;
		content_type?: string | null;
		size?: number | null;
	};

	type DocumentCollectionRef = {
		id?: string | null;
		name?: string | null;
	};

	type DocumentLike = {
		id?: string | null;
		title?: string | null;
		filename?: string | null;
		description?: string | null;
		summary?: string | null;
		source?: string | null;
		status?: string | null;
		processing_status?: string | null;
		document_type?: string | null;
		document_status?: string | null;
		is_locked_by_status?: boolean | null;
		collection_name?: string | null;
		collection?: DocumentCollectionRef | null;
		primary_collection?: DocumentCollectionRef | null;
		meta?: DocumentMeta | null;
		metadata?: DocumentMeta | null;
		data?: Record<string, any> | null;
		updated_at?: number | null;
	};

	type CollectionLike = {
		id: string;
		name: string;
		description?: string | null;
		write_access?: boolean;
		updated_at?: number | null;
		file_count?: number | null;
		folder_count?: number | null;
		files?: unknown[] | null;
		folders?: unknown[] | null;
		data?: {
			file_ids?: unknown[] | null;
		} | null;
		user?: {
			name?: string | null;
			email?: string | null;
		} | null;
	};

	type SearchResult<T> = {
		items?: T[] | null;
		total?: number | null;
	};

	type MetadataDraft = {
		title: string;
		description: string;
		summary: string;
		author: string;
		source: string;
		language: string;
		document_type: string;
		document_status: string;
		version: string;
		tags: string;
		entities: string;
		suggested_collection_hints: string;
	};

	let loaded = false;
	let showDeleteConfirm = false;
	let selectedItem: CollectionLike | null = null;

	let page = 1;
	let query = '';
	let searchDebounceTimer: ReturnType<typeof setTimeout>;
	let viewOption = '';

	let items: CollectionLike[] | null = null;
	let total: number | null = null;
	let allItemsLoaded = false;
	let itemsLoading = false;

	let recentDocuments: DocumentLike[] = [];
	let recentDocumentsTotal = 0;
	let recentDocumentsLoading = false;
	let showCollectionDocumentsList = false;
	let allDocumentsList: DocumentLike[] = [];
	let allDocumentsListTotal = 0;
	let allDocumentsListLoading = false;
	let allDocumentsListQuery = '';
	let documentTypeFilter = '';
	let documentStatusFilter = '';
	let lockedFilter = '';
	let selectedAllDocumentIds: string[] = [];
	let selectedAllDocuments: DocumentLike[] = [];
	let reprocessingId: string | null = null;
	let showDocumentPreviewModal = false;
	let documentPreviewItem: Record<string, any> | null = null;
	let showMetadataModal = false;
	let metadataLoading = false;
	let metadataSaving = false;
	let metadataDocument: DocumentLike | null = null;
	let metadataInstruction = '';
	let metadataDraft: MetadataDraft = {
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
		suggested_collection_hints: ''
	};
	$: documentFilterSource =
		allDocumentsList.length > 0 ? allDocumentsList : (recentDocuments ?? []);
	$: availableDocumentTypes = Array.from(
		new Set(documentFilterSource.map((document) => getDocumentType(document)).filter(Boolean))
	).sort((left, right) => left.localeCompare(right));
	$: availableDocumentStatuses = Array.from(
		new Set(documentFilterSource.map((document) => getDocumentStatus(document)).filter(Boolean))
	).sort((left, right) => left.localeCompare(right));

	const getDocumentTitle = (item: DocumentLike | null | undefined) =>
		item?.title || item?.meta?.name || item?.filename || 'Documento';
	const getDocumentDescription = (item: DocumentLike | null | undefined) =>
		item?.description || item?.summary || item?.meta?.description || '';
	const getDocumentCollection = (item: DocumentLike | null | undefined) =>
		item?.collection?.name || item?.primary_collection?.name || item?.collection_name || '';
	const getDocumentType = (item: DocumentLike | null | undefined) =>
		item?.document_type ||
		item?.metadata?.document_type ||
		item?.meta?.document_type ||
		'Documento';
	const getDocumentStatus = (item: DocumentLike | null | undefined) =>
		item?.document_status ||
		item?.metadata?.document_status ||
		item?.meta?.document_status ||
		'Em elaboração';
	const isDocumentLockedByStatus = (item: DocumentLike | null | undefined) =>
		Boolean(
			item?.is_locked_by_status ||
			item?.metadata?.is_locked_by_status ||
			item?.meta?.is_locked_by_status
		);
	const getCollectionFileCount = (item: CollectionLike | null | undefined) =>
		item?.file_count ?? item?.files?.length ?? item?.data?.file_ids?.length ?? 0;
	const getCollectionFolderCount = (item: CollectionLike | null | undefined) =>
		item?.folder_count ?? item?.folders?.length ?? 0;
	const toDelimitedValue = (value: string[] | string | null | undefined) =>
		Array.isArray(value) ? value.join(', ') : (value ?? '');
	const toList = (value: string) =>
		value
			.split(',')
			.map((item) => item.trim())
			.filter(Boolean);
	const buildDocumentChatFile = (document: DocumentLike) => ({
		...document,
		type: 'file',
		name: getDocumentTitle(document),
		filename: document?.filename || getDocumentTitle(document),
		status: document?.processing_status || document?.status || 'processed',
		collection_name: getDocumentCollection(document) || '',
		meta: {
			...(document?.meta ?? {}),
			...(document?.metadata ?? {}),
			name: getDocumentTitle(document),
			title: document?.title || getDocumentTitle(document),
			description: getDocumentDescription(document) || document?.metadata?.description || '',
			source:
				document?.source ||
				document?.metadata?.source ||
				document?.meta?.source ||
				document?.filename ||
				'',
			document_type:
				document?.document_type ||
				document?.metadata?.document_type ||
				document?.meta?.document_type ||
				'',
			document_status:
				document?.document_status ||
				document?.metadata?.document_status ||
				document?.meta?.document_status ||
				'Em elaboração',
			is_locked_by_status:
				document?.is_locked_by_status ||
				document?.metadata?.is_locked_by_status ||
				document?.meta?.is_locked_by_status ||
				false
		},
		data: document?.data ?? {}
	});
	const toggleAllDocumentsSelection = (document: DocumentLike) => {
		if (!document?.id) return;
		selectedAllDocumentIds = selectedAllDocumentIds.includes(document.id)
			? selectedAllDocumentIds.filter((id) => id !== document.id)
			: [...selectedAllDocumentIds, document.id];
	};
	const openChatWithDocuments = async (documents: DocumentLike[]) => {
		const selectedDocuments = (documents ?? []).filter((document) => document?.id);
		if (selectedDocuments.length === 0) {
			toast.info('Seleciona pelo menos um documento para conversar.');
			return;
		}

		const input = {
			prompt: '',
			files: selectedDocuments.map((document) => buildDocumentChatFile(document)),
			selectedToolIds: [],
			selectedFilterIds: [],
			webSearchEnabled: false,
			imageGenerationEnabled: false,
			codeInterpreterEnabled: false
		};

		sessionStorage.setItem('chat-input', JSON.stringify(input));
		await goto('/');
	};
	$: selectedAllDocuments = allDocumentsList.filter((document) =>
		selectedAllDocumentIds.includes(document?.id ?? '')
	);
	$: selectedAllDocumentIds = selectedAllDocumentIds.filter((id) =>
		allDocumentsList.some((document) => document?.id === id)
	);

	$: if (query !== undefined) {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			init();
		}, 300);
	}

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
	});

	$: if (viewOption !== undefined) {
		init();
	}

	$: if (
		documentTypeFilter !== undefined &&
		documentStatusFilter !== undefined &&
		lockedFilter !== undefined
	) {
		init();
	}

	const reset = () => {
		page = 1;
		items = null;
		total = null;
		allItemsLoaded = false;
		itemsLoading = false;
	};

	const loadMoreItems = async () => {
		if (allItemsLoaded) return;
		page += 1;
		await getCollectionsPage();
	};

	const getCollectionsPage = async () => {
		itemsLoading = true;
		const res = await searchKnowledgeBases(localStorage.token, query, viewOption, page).catch(
			() => {
				return null;
			}
		);

		if (res) {
			const typedRes = res as SearchResult<CollectionLike>;
			total = typedRes.total ?? 0;
			const pageItems = typedRes.items ?? [];

			allItemsLoaded = (pageItems ?? []).length === 0;
			items = items ? [...items, ...pageItems] : pageItems;
		}

		itemsLoading = false;
		return res;
	};

	const getRecentDocuments = async () => {
		recentDocumentsLoading = true;

		const res = await getDocuments(localStorage.token, {
			query,
			document_type: documentTypeFilter || null,
			document_status: documentStatusFilter || null,
			locked: lockedFilter === '' ? null : lockedFilter === 'locked',
			page: 1,
			limit: 12
		}).catch(() => null);

		if (res) {
			const typedRes = res as SearchResult<DocumentLike>;
			recentDocuments = typedRes.items ?? [];
			recentDocumentsTotal = typedRes.total ?? recentDocuments.length;
		} else {
			recentDocuments = [];
			recentDocumentsTotal = 0;
		}

		recentDocumentsLoading = false;
	};

	const loadAllDocumentsList = async (force = false) => {
		const normalizedQuery = query.trim();
		if (!force && allDocumentsListQuery === normalizedQuery && allDocumentsList.length > 0) {
			return;
		}

		allDocumentsListLoading = true;

		try {
			let nextPage = 1;
			let mergedItems: DocumentLike[] = [];
			let totalItems = 0;
			const limit = 100;

			while (nextPage <= 10) {
				const res = await getDocuments(localStorage.token, {
					query: normalizedQuery || null,
					document_type: documentTypeFilter || null,
					document_status: documentStatusFilter || null,
					locked: lockedFilter === '' ? null : lockedFilter === 'locked',
					page: nextPage,
					limit
				}).catch(() => null);

				if (!res) break;

				const typedRes = res as SearchResult<DocumentLike>;
				const pageItems = typedRes.items ?? [];
				mergedItems = [...mergedItems, ...pageItems];
				totalItems = typedRes.total ?? mergedItems.length;

				if (pageItems.length === 0 || mergedItems.length >= totalItems) {
					break;
				}

				nextPage += 1;
			}

			allDocumentsList = mergedItems;
			allDocumentsListTotal = totalItems || mergedItems.length;
			allDocumentsListQuery = normalizedQuery;
		} finally {
			allDocumentsListLoading = false;
		}
	};

	const init = async () => {
		if (!loaded) return;
		reset();
		await Promise.all([
			getCollectionsPage(),
			getRecentDocuments(),
			showCollectionDocumentsList ? loadAllDocumentsList(true) : Promise.resolve()
		]);
	};

	const deleteHandler = async (item: CollectionLike | null) => {
		if (!item) return;

		const res = await deleteKnowledgeById(localStorage.token, item.id).catch((e) => {
			toast.error(`${e}`);
		});

		if (res) {
			toast.success('Colecao removida com sucesso.');
			await init();
		}
	};

	const exportHandler = async (item: CollectionLike) => {
		try {
			const blob = await exportKnowledgeById(localStorage.token, item.id);
			if (blob) {
				const url = URL.createObjectURL(blob);
				const a = document.createElement('a');
				a.href = url;
				a.download = `${item.name}.zip`;
				document.body.appendChild(a);
				a.click();
				document.body.removeChild(a);
				URL.revokeObjectURL(url);
				toast.success('Colecao exportada com sucesso.');
			}
		} catch (e) {
			toast.error(`${e}`);
		}
	};

	const reprocessHandler = async (item: CollectionLike) => {
		reprocessingId = item.id;
		const res = await resetKnowledgeById(localStorage.token, item.id).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			toast.success('Reprocessamento iniciado.');
			await init();
		}

		reprocessingId = null;
	};

	const openCollection = async (item: CollectionLike) => {
		await goto(`/workspace/knowledge/${item.id}`);
	};

	const openChatWithCollection = async (item: CollectionLike) => {
		const input = {
			prompt: '',
			files: [
				{
					...item,
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

	const openDocument = async (document: DocumentLike) => {
		const collectionId = document?.collection?.id || document?.primary_collection?.id;
		if (collectionId) {
			await goto(`/workspace/knowledge/${collectionId}`);
			return;
		}
		await goto('/workspace/knowledge');
	};

	const openDocumentPreview = (document: DocumentLike) => {
		if (!document?.id) {
			toast.error('Documento sem arquivo associado.');
			return;
		}

		documentPreviewItem = {
			...buildDocumentChatFile(document),
			id: document.id,
			name: document?.filename || getDocumentTitle(document),
			size: document?.metadata?.size || document?.meta?.size || null,
			meta: {
				...(document?.meta ?? {}),
				...(document?.metadata ?? {}),
				name: document?.filename || getDocumentTitle(document),
				content_type:
					document?.metadata?.content_type || document?.meta?.content_type || 'text/markdown'
			}
		};
		showDocumentPreviewModal = true;
	};

	const toggleCollectionDocumentsList = async () => {
		showCollectionDocumentsList = !showCollectionDocumentsList;
		if (showCollectionDocumentsList) {
			await loadAllDocumentsList(true);
		}
	};

	const hydrateMetadataDraft = (payload: Record<string, any> | null | undefined) => {
		metadataDraft = {
			title: payload?.title ?? '',
			description: payload?.description ?? '',
			summary: payload?.summary ?? '',
			author: payload?.author ?? '',
			source: payload?.source ?? '',
			language: payload?.language ?? '',
			document_type: payload?.document_type ?? '',
			document_status: payload?.document_status ?? 'Em elaboração',
			version: String(payload?.version ?? '1'),
			tags: toDelimitedValue(payload?.tags),
			entities: toDelimitedValue(payload?.entities),
			suggested_collection_hints: toDelimitedValue(payload?.suggested_collection_hints)
		};
	};

	const openMetadataModal = (document: DocumentLike) => {
		metadataDocument = document;
		metadataInstruction = '';
		hydrateMetadataDraft(document);
		showMetadataModal = true;
	};

	const generateMetadataHandler = async () => {
		if (!metadataDocument?.id) return;
		metadataLoading = true;

		const res = await suggestDocumentMetadataById(localStorage.token, metadataDocument.id, {
			instruction: metadataInstruction
		}).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			hydrateMetadataDraft(res);
			toast.success('Sugestao de metadados gerada.');
		}

		metadataLoading = false;
	};

	const saveMetadataHandler = async () => {
		if (!metadataDocument?.id) return;
		metadataSaving = true;

		const res = await updateDocumentMetadataById(localStorage.token, metadataDocument.id, {
			title: metadataDraft.title,
			description: metadataDraft.description,
			summary: metadataDraft.summary,
			author: metadataDraft.author,
			source: metadataDraft.source,
			language: metadataDraft.language,
			document_type: metadataDraft.document_type,
			document_status: metadataDraft.document_status,
			version: metadataDraft.version,
			tags: toList(metadataDraft.tags),
			entities: toList(metadataDraft.entities),
			suggested_collection_hints: toList(metadataDraft.suggested_collection_hints)
		}).catch((e) => {
			toast.error(`${e}`);
			return null;
		});

		if (res) {
			metadataDocument = res;
			hydrateMetadataDraft(res);
			toast.success('Metadados salvos.');
			await init();
		}

		metadataSaving = false;
	};

	onMount(async () => {
		viewOption = localStorage?.workspaceViewOption || '';
		loaded = true;
		await init();
	});
</script>

<svelte:head>
	<title>Documentos • {$WEBUI_NAME}</title>
</svelte:head>

{#if loaded}
	<DeleteConfirmDialog
		bind:show={showDeleteConfirm}
		title="Remover colecao?"
		on:confirm={() => {
			deleteHandler(selectedItem);
		}}
	/>

	{#if showMetadataModal && metadataDocument}
		<div class="dochat-metadata-modal-backdrop">
			<div class="dochat-metadata-modal">
				<div class="dochat-metadata-modal-head">
					<div>
						<div class="dochat-documents-kicker">Metadados com IA</div>
						<h2>{getDocumentTitle(metadataDocument)}</h2>
						<p>Revise a sugestao, ajuste os campos e salve no documento.</p>
					</div>

					<button
						type="button"
						class="dochat-documents-clear"
						aria-label="Fechar metadados"
						on:click={() => {
							showMetadataModal = false;
						}}
					>
						<XMark className="size-4" strokeWidth="2" />
					</button>
				</div>

				<label class="dochat-metadata-field dochat-metadata-field-wide">
					<span>Instrucao para a IA</span>
					<textarea
						bind:value={metadataInstruction}
						placeholder="Ex.: Foque em termos juridicos, entidades e taxonomia de compliance."
					></textarea>
				</label>

				<div class="dochat-metadata-actions">
					<button
						type="button"
						class="dochat-documents-primary dochat-documents-primary-soft"
						on:click={generateMetadataHandler}
						disabled={metadataLoading}
					>
						{metadataLoading ? 'Gerando...' : 'Gerar metadados com IA'}
					</button>
					<button
						type="button"
						class="dochat-documents-primary"
						on:click={saveMetadataHandler}
						disabled={metadataSaving}
					>
						{metadataSaving ? 'Salvando...' : 'Salvar'}
					</button>
				</div>

				<div class="dochat-metadata-grid">
					<label class="dochat-metadata-field dochat-metadata-field-wide">
						<span>Titulo</span>
						<input bind:value={metadataDraft.title} />
					</label>
					<label class="dochat-metadata-field dochat-metadata-field-wide">
						<span>Descricao</span>
						<textarea bind:value={metadataDraft.description}></textarea>
					</label>
					<label class="dochat-metadata-field dochat-metadata-field-wide">
						<span>Resumo</span>
						<textarea bind:value={metadataDraft.summary}></textarea>
					</label>
					<label class="dochat-metadata-field">
						<span>Autor</span>
						<input bind:value={metadataDraft.author} />
					</label>
					<label class="dochat-metadata-field">
						<span>Origem</span>
						<input bind:value={metadataDraft.source} />
					</label>
					<label class="dochat-metadata-field">
						<span>Idioma</span>
						<input bind:value={metadataDraft.language} />
					</label>
					<label class="dochat-metadata-field">
						<span>Tipo de documento</span>
						<input bind:value={metadataDraft.document_type} />
					</label>
					<label class="dochat-metadata-field">
						<span>Status</span>
						<select bind:value={metadataDraft.document_status}>
							<option value="Em elaboração">Em elaboração</option>
							<option value="Em revisão">Em revisão</option>
							<option value="Concluído">Concluído</option>
							<option value="Finalizado">Finalizado</option>
						</select>
					</label>
					<label class="dochat-metadata-field">
						<span>Versao</span>
						<input bind:value={metadataDraft.version} />
					</label>
					<label class="dochat-metadata-field dochat-metadata-field-wide">
						<span>Tags</span>
						<input bind:value={metadataDraft.tags} placeholder="tag1, tag2, tag3" />
					</label>
					<label class="dochat-metadata-field dochat-metadata-field-wide">
						<span>Entidades</span>
						<input bind:value={metadataDraft.entities} placeholder="Pessoa, Empresa, Sistema" />
					</label>
					<label class="dochat-metadata-field dochat-metadata-field-wide">
						<span>Pistas de colecao</span>
						<input
							bind:value={metadataDraft.suggested_collection_hints}
							placeholder="Financeiro, Compliance, Pesquisa"
						/>
					</label>
				</div>
			</div>
		</div>
	{/if}

	{#if documentPreviewItem}
		<FileItemModal bind:show={showDocumentPreviewModal} bind:item={documentPreviewItem} />
	{/if}

	<div class="dochat-documents-page">
		<section class="dochat-documents-hero">
			<div>
				<div class="dochat-documents-kicker">Documentos</div>
				<h1>Biblioteca documental</h1>
				<p>
					Organize colecoes, acompanhe o estado de processamento e recupere documentos com leitura
					leve.
				</p>
			</div>

			{#if $user?.role === 'admin'}
				<div class="dochat-documents-actions">
					<a class="dochat-documents-primary" href="/workspace/knowledge/create">
						<Plus className="size-3.5" strokeWidth="2.5" />
						<span>Nova colecao</span>
					</a>
				</div>
			{/if}
		</section>

		<section class="dochat-documents-summary">
			<div class="dochat-summary-card">
				<span>Colecoes</span>
				<strong>{total ?? 0}</strong>
			</div>
			<div class="dochat-summary-card">
				<span>Documentos recentes</span>
				<strong>{recentDocumentsTotal}</strong>
			</div>
			<div class="dochat-summary-card">
				<span>Busca atual</span>
				<strong>{query ? 'Filtrada' : 'Completa'}</strong>
			</div>
		</section>

		<section class="dochat-documents-panel">
			<div class="dochat-documents-toolbar">
				<div class="dochat-documents-search">
					<Search className="size-4" />
					<input
						bind:value={query}
						aria-label="Buscar em documentos"
						placeholder="Buscar em colecoes e documentos"
					/>

					{#if query}
						<button
							type="button"
							class="dochat-documents-clear"
							aria-label="Limpar busca"
							on:click={() => {
								query = '';
							}}
						>
							<XMark className="size-3.5" strokeWidth="2" />
						</button>
					{/if}
				</div>

				<div class="dochat-documents-view">
					<ViewSelector
						bind:value={viewOption}
						onChange={async (value) => {
							localStorage.workspaceViewOption = value;
							await tick();
						}}
					/>
				</div>
			</div>

			<div class="dochat-documents-toolbar">
				<div class="dochat-documents-filters">
					<select bind:value={documentTypeFilter} aria-label="Filtrar por tipologia">
						<option value="">Todas as tipologias</option>
						{#each availableDocumentTypes as documentType}
							<option value={documentType}>{documentType}</option>
						{/each}
					</select>
					<select bind:value={documentStatusFilter} aria-label="Filtrar por status">
						<option value="">Todos os status</option>
						{#each availableDocumentStatuses as documentStatus}
							<option value={documentStatus}>{documentStatus}</option>
						{/each}
					</select>
					<select bind:value={lockedFilter} aria-label="Filtrar por bloqueio">
						<option value="">Travados e editaveis</option>
						<option value="locked">Somente travados</option>
						<option value="editable">Somente editaveis</option>
					</select>
				</div>
			</div>

			<div class="dochat-documents-content">
				<div class="dochat-documents-section">
					<div class="dochat-documents-section-head">
						<div>
							<h2>Documentos recentes</h2>
							<p>Arquivos e itens indexados mais recentes na tua biblioteca.</p>
						</div>
					</div>

					{#if recentDocumentsLoading}
						<div class="dochat-documents-loading">
							<Spinner className="size-4" />
							<span>Carregando documentos...</span>
						</div>
					{:else if recentDocuments.length === 0}
						<div class="dochat-documents-empty">
							<div>Nenhum documento recente.</div>
							<p>Adicione conteudo a uma colecao para começar a indexar sua biblioteca.</p>
						</div>
					{:else}
						<div class="dochat-document-grid">
							{#each recentDocuments.slice(0, 8) as document}
								<div class="dochat-document-card">
									<div class="dochat-document-top">
										<div class="dochat-document-icon">
											<DocumentPage className="size-4" />
										</div>
										{#if document?.updated_at}
											<Tooltip content={dayjs(document.updated_at * 1000).format('LLLL')}>
												<span>{dayjs(document.updated_at * 1000).fromNow()}</span>
											</Tooltip>
										{/if}
									</div>

									<div class="dochat-document-title">{getDocumentTitle(document)}</div>
									<div class="dochat-document-description">
										{getDocumentDescription(document) ||
											'Documento disponivel para busca e contexto.'}
									</div>

									<div class="dochat-document-meta">
										{#if getDocumentCollection(document)}
											<span>{getDocumentCollection(document)}</span>
										{/if}
										<span class="dochat-document-status">{getDocumentType(document)}</span>
										<span class="dochat-document-status">{getDocumentStatus(document)}</span>
										{#if isDocumentLockedByStatus(document)}
											<span class="dochat-document-status">Travado</span>
										{/if}
										{#if document?.metadata?.size || document?.meta?.size}
											<span>{formatFileSize(document?.metadata?.size || document?.meta?.size)}</span
											>
										{/if}
									</div>

									<div class="dochat-document-actions">
										<button
											type="button"
											class="dochat-collection-action"
											on:click={() => openDocument(document)}
										>
											Abrir
										</button>
										<button
											type="button"
											class="dochat-collection-action"
											on:click={() => openDocumentPreview(document)}
										>
											Arquivo + Markdown
										</button>
										<button
											type="button"
											class="dochat-collection-action dochat-collection-action-accent"
											on:click={() => openMetadataModal(document)}
										>
											Metadados IA
										</button>
									</div>
								</div>
							{/each}
						</div>
					{/if}
				</div>

				<div class="dochat-documents-section">
					<div class="dochat-documents-section-head">
						<div>
							<h2>Colecoes</h2>
							<p>Metadados, acesso e estado da tua estrutura documental.</p>
						</div>

						<button
							type="button"
							class="dochat-collection-action dochat-collection-action-accent"
							on:click={toggleCollectionDocumentsList}
						>
							{showCollectionDocumentsList
								? 'Ocultar lista de documentos'
								: 'Ver todos os documentos em lista'}
						</button>
					</div>

					{#if items !== null && total !== null}
						{#if (items ?? []).length !== 0}
							<div class="dochat-collection-list">
								{#each items as item}
									<div class="dochat-collection-card">
										<div class="dochat-collection-head">
											<div class="dochat-collection-badges">
												<Badge type="success" content="Colecao" />

												{#if !item?.write_access}
													<Badge type="muted" content="Somente leitura" />
												{/if}
											</div>

											{#if item?.updated_at}
												<Tooltip content={dayjs(item.updated_at * 1000).format('LLLL')}>
													<span class="dochat-collection-updated">
														Atualizada {dayjs(item.updated_at * 1000).fromNow()}
													</span>
												</Tooltip>
											{/if}
										</div>

										<div class="dochat-collection-title-row">
											<div>
												<div class="dochat-collection-title">{item.name}</div>
												<div class="dochat-collection-description">
													{item?.description ||
														'Colecao pronta para anexos, contexto e reprocessamento.'}
												</div>
											</div>
										</div>

										<div class="dochat-collection-meta">
											<div>
												<span>Arquivos</span>
												<strong>{getCollectionFileCount(item)}</strong>
											</div>
											<div>
												<span>Pastas</span>
												<strong>{getCollectionFolderCount(item)}</strong>
											</div>
											<div>
												<span>Embedding</span>
												<strong
													>{getCollectionFileCount(item) > 0 ? 'Disponivel' : 'Pendente'}</strong
												>
											</div>
											<div>
												<span>Responsavel</span>
												<strong>
													{capitalizeFirstLetter(
														item?.user?.name ?? item?.user?.email ?? 'Deleted User'
													)}
												</strong>
											</div>
										</div>

										<div class="dochat-collection-actions">
											<button
												type="button"
												class="dochat-collection-action"
												on:click={() => openCollection(item)}
											>
												Abrir
											</button>
											<button
												type="button"
												class="dochat-collection-action dochat-collection-action-accent"
												on:click={() => openChatWithCollection(item)}
											>
												Abrir chat com esta colecao
											</button>
											<button
												type="button"
												class="dochat-collection-action"
												on:click={() => openCollection(item)}
											>
												Editar metadados
											</button>

											{#if item?.write_access || $user?.role === 'admin'}
												<button
													type="button"
													class="dochat-collection-action"
													on:click={() => reprocessHandler(item)}
													disabled={reprocessingId === item.id}
												>
													{reprocessingId === item.id ? 'Reprocessando...' : 'Reprocessar'}
												</button>
											{/if}

											{#if $user?.role === 'admin'}
												<button
													type="button"
													class="dochat-collection-action"
													on:click={() => exportHandler(item)}
												>
													Exportar
												</button>
											{/if}

											{#if item?.write_access || $user?.role === 'admin'}
												<button
													type="button"
													class="dochat-collection-action dochat-collection-action-danger"
													on:click={() => {
														selectedItem = item;
														showDeleteConfirm = true;
													}}
												>
													Remover
												</button>
											{/if}
										</div>
									</div>
								{/each}
							</div>

							{#if !allItemsLoaded}
								<Loader
									on:visible={() => {
										if (!itemsLoading) {
											loadMoreItems();
										}
									}}
								>
									<div class="dochat-documents-loading">
										<Spinner className="size-4" />
										<span>Carregando mais colecoes...</span>
									</div>
								</Loader>
							{/if}
						{:else}
							<div class="dochat-documents-empty">
								<div>Nenhuma colecao encontrada.</div>
								<p>Ajuste a busca ou crie uma nova colecao para organizar seus documentos.</p>
							</div>
						{/if}
					{:else}
						<div class="dochat-documents-loading">
							<Spinner className="size-4" />
							<span>Carregando biblioteca...</span>
						</div>
					{/if}

					{#if showCollectionDocumentsList}
						<div class="dochat-documents-list-panel">
							<div class="dochat-documents-section-head">
								<div>
									<h2>Todos os documentos</h2>
									<p>{allDocumentsListTotal} documentos visiveis em formato de lista.</p>
								</div>

								<button
									type="button"
									class="dochat-collection-action dochat-collection-action-accent"
									on:click={() => openChatWithDocuments(selectedAllDocuments)}
									disabled={selectedAllDocuments.length === 0}
								>
									{selectedAllDocuments.length > 0
										? `Conversar com ${selectedAllDocuments.length} documento(s)`
										: 'Conversar com selecionados'}
								</button>
							</div>

							{#if allDocumentsListLoading}
								<div class="dochat-documents-loading">
									<Spinner className="size-4" />
									<span>Carregando documentos...</span>
								</div>
							{:else if allDocumentsList.length === 0}
								<div class="dochat-documents-empty">
									<div>Nenhum documento disponivel.</div>
									<p>Adicione documentos ou ajuste a pesquisa para ver a lista completa.</p>
								</div>
							{:else}
								<div class="dochat-documents-table-wrap">
									<table class="dochat-documents-table">
										<thead>
											<tr>
												<th>Chat</th>
												<th>Documento</th>
												<th>Colecao</th>
												<th>Tipologia</th>
												<th>Status</th>
												<th>Bloqueio</th>
												<th>Atualizado</th>
												<th>Acoes</th>
											</tr>
										</thead>
										<tbody>
											{#each allDocumentsList as document}
												<tr>
													<td>
														<input
															type="checkbox"
															checked={selectedAllDocumentIds.includes(document.id ?? '')}
															aria-label={`Selecionar ${getDocumentTitle(document)} para conversa`}
															on:change={() => toggleAllDocumentsSelection(document)}
														/>
													</td>
													<td>
														<div class="dochat-documents-table-title">
															<strong>{getDocumentTitle(document)}</strong>
															<span>{document?.filename || 'Sem ficheiro associado'}</span>
														</div>
													</td>
													<td>{getDocumentCollection(document) || 'Sem colecao'}</td>
													<td>{getDocumentType(document)}</td>
													<td>
														<span class="dochat-document-status">
															{getDocumentStatus(document)}
														</span>
													</td>
													<td>
														<span class="dochat-document-status">
															{isDocumentLockedByStatus(document) ? 'Travado' : 'Editavel'}
														</span>
													</td>
													<td>
														{#if document?.updated_at}
															<Tooltip content={dayjs(document.updated_at * 1000).format('LLLL')}>
																<span>{dayjs(document.updated_at * 1000).fromNow()}</span>
															</Tooltip>
														{:else}
															<span>Sem data</span>
														{/if}
													</td>
													<td>
														<div class="dochat-document-actions">
															<button
																type="button"
																class="dochat-collection-action dochat-collection-action-accent"
																on:click={() => openChatWithDocuments([document])}
															>
																Conversar
															</button>
															<button
																type="button"
																class="dochat-collection-action"
																on:click={() => openDocument(document)}
															>
																Abrir
															</button>
															<button
																type="button"
																class="dochat-collection-action"
																on:click={() => openDocumentPreview(document)}
															>
																Arquivo + Markdown
															</button>
															<button
																type="button"
																class="dochat-collection-action dochat-collection-action-accent"
																on:click={() => openMetadataModal(document)}
															>
																Metadados IA
															</button>
														</div>
													</td>
												</tr>
											{/each}
										</tbody>
									</table>
								</div>
							{/if}
						</div>
					{/if}
				</div>
			</div>
		</section>

		<div class="dochat-documents-helper">
			Use <code>#</code> no composer do chat ou o atalho de cada colecao para abrir conversas com contexto
			documental imediato.
		</div>
	</div>
{:else}
	<div class="dochat-documents-loading dochat-documents-page">
		<Spinner className="size-5" />
	</div>
{/if}

<style>
	.dochat-documents-page {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		padding: 1rem 0 1.25rem;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-documents-hero,
	.dochat-documents-panel,
	.dochat-summary-card {
		border: 1px solid rgba(221, 214, 202, 0.86);
		background: rgba(255, 255, 255, 0.84);
		box-shadow: 0 18px 36px rgba(84, 74, 58, 0.06);
		backdrop-filter: blur(14px);
	}

	.dochat-documents-hero {
		display: flex;
		align-items: end;
		justify-content: space-between;
		gap: 1rem;
		padding: 1.5rem;
		border-radius: 1.7rem;
	}

	.dochat-documents-kicker {
		font-size: 0.76rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--dochat-accent, #6f8a64);
	}

	.dochat-documents-hero h1 {
		font-size: clamp(1.75rem, 3vw, 2.45rem);
		font-weight: 700;
		letter-spacing: -0.03em;
	}

	.dochat-documents-hero p {
		margin-top: 0.35rem;
		max-width: 42rem;
		line-height: 1.6;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-documents-primary {
		display: inline-flex;
		align-items: center;
		gap: 0.55rem;
		padding: 0.9rem 1.15rem;
		border-radius: 9999px;
		background: var(--dochat-accent, #6f8a64);
		color: white;
		font-size: 0.9rem;
		font-weight: 700;
	}

	.dochat-documents-summary {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 1rem;
	}

	.dochat-summary-card {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		padding: 1rem 1.15rem;
		border-radius: 1.35rem;
	}

	.dochat-summary-card span {
		font-size: 0.8rem;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-summary-card strong {
		font-size: 1.5rem;
		font-weight: 700;
		letter-spacing: -0.03em;
	}

	.dochat-documents-panel {
		border-radius: 1.7rem;
		padding: 1rem;
	}

	.dochat-documents-toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
	}

	.dochat-documents-filters {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
	}

	.dochat-documents-filters select {
		min-width: 12rem;
		padding: 0.8rem 0.95rem;
		border: 1px solid rgba(231, 225, 216, 0.96);
		border-radius: 1rem;
		background: rgba(251, 249, 245, 0.92);
		color: var(--dochat-text, #1d1d1f);
		outline: none;
	}

	.dochat-documents-search {
		display: flex;
		align-items: center;
		gap: 0.65rem;
		flex: 1;
		padding: 0.9rem 1rem;
		border: 1px solid rgba(231, 225, 216, 0.96);
		border-radius: 1.25rem;
		background: rgba(251, 249, 245, 0.92);
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-documents-search input {
		flex: 1;
		min-width: 0;
		background: transparent;
		outline: none;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-documents-search input::placeholder {
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-documents-clear {
		padding: 0.3rem;
		border-radius: 9999px;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-documents-clear:hover {
		background: rgba(247, 244, 238, 0.96);
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-documents-content {
		display: flex;
		flex-direction: column;
		gap: 1.5rem;
		margin-top: 1rem;
	}

	.dochat-documents-section {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.dochat-documents-section-head {
		display: flex;
		align-items: end;
		justify-content: space-between;
		gap: 1rem;
	}

	.dochat-documents-section-head h2 {
		font-size: 1.15rem;
		font-weight: 700;
		letter-spacing: -0.02em;
	}

	.dochat-documents-section-head p {
		margin-top: 0.2rem;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-document-grid {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 1rem;
	}

	.dochat-document-card,
	.dochat-collection-card {
		border: 1px solid rgba(231, 225, 216, 0.9);
		border-radius: 1.35rem;
		background: rgba(251, 249, 245, 0.82);
	}

	.dochat-document-card {
		display: flex;
		flex-direction: column;
		gap: 0.7rem;
		padding: 1rem;
	}

	.dochat-document-top,
	.dochat-collection-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
	}

	.dochat-document-top span,
	.dochat-collection-updated {
		font-size: 0.78rem;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-document-icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2rem;
		height: 2rem;
		border-radius: 9999px;
		background: rgba(232, 239, 228, 0.86);
		color: var(--dochat-accent, #6f8a64);
	}

	.dochat-document-title,
	.dochat-collection-title {
		font-size: 1rem;
		font-weight: 700;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-document-description,
	.dochat-collection-description {
		color: var(--dochat-text-soft, #5c5c62);
		line-height: 1.6;
	}

	.dochat-document-meta,
	.dochat-document-actions,
	.dochat-collection-badges,
	.dochat-collection-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.55rem;
	}

	.dochat-document-meta span {
		padding: 0.24rem 0.55rem;
		border-radius: 9999px;
		background: rgba(247, 244, 238, 0.96);
		font-size: 0.76rem;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-document-status {
		background: rgba(232, 239, 228, 0.9) !important;
		color: var(--dochat-accent, #6f8a64) !important;
		font-weight: 700;
	}

	.dochat-collection-list {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.dochat-documents-list-panel {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		border: 1px solid rgba(231, 225, 216, 0.9);
		border-radius: 1.35rem;
		background: rgba(251, 249, 245, 0.82);
		padding: 1rem;
	}

	.dochat-documents-table-wrap {
		overflow-x: auto;
		border-radius: 1.15rem;
		border: 1px solid rgba(221, 214, 202, 0.86);
	}

	.dochat-documents-table {
		width: 100%;
		border-collapse: collapse;
		background: rgba(255, 255, 255, 0.84);
	}

	.dochat-documents-table th,
	.dochat-documents-table td {
		padding: 0.9rem 1rem;
		border-bottom: 1px solid rgba(231, 225, 216, 0.9);
		text-align: left;
		font-size: 0.9rem;
		vertical-align: top;
	}

	.dochat-documents-table th {
		font-size: 0.76rem;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--dochat-accent, #6f8a64);
		background: rgba(232, 239, 228, 0.38);
	}

	.dochat-documents-table-title {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
	}

	.dochat-documents-table-title strong {
		color: var(--dochat-text, #1d1d1f);
		font-weight: 700;
	}

	.dochat-documents-table-title span {
		font-size: 0.8rem;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-collection-card {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		padding: 1rem 1.1rem 1.1rem;
	}

	.dochat-collection-title-row {
		display: flex;
		justify-content: space-between;
		gap: 1rem;
	}

	.dochat-collection-meta {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 0.75rem;
	}

	.dochat-collection-meta div {
		padding: 0.85rem 0.95rem;
		border-radius: 1rem;
		background: rgba(255, 255, 255, 0.72);
		border: 1px solid rgba(231, 225, 216, 0.92);
	}

	.dochat-collection-meta span {
		display: block;
		font-size: 0.76rem;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-collection-meta strong {
		display: block;
		margin-top: 0.25rem;
		font-size: 0.95rem;
		font-weight: 700;
	}

	.dochat-collection-action {
		padding: 0.7rem 0.95rem;
		border-radius: 9999px;
		background: rgba(247, 244, 238, 0.96);
		color: var(--dochat-text-soft, #5c5c62);
		font-size: 0.82rem;
		font-weight: 700;
		transition:
			background-color 160ms ease,
			color 160ms ease,
			transform 160ms ease;
	}

	.dochat-collection-action:hover {
		background: rgba(232, 239, 228, 0.9);
		color: var(--dochat-accent, #6f8a64);
		transform: translateY(-1px);
	}

	.dochat-collection-action-accent {
		background: rgba(232, 239, 228, 0.96);
		color: var(--dochat-accent, #6f8a64);
	}

	.dochat-collection-action-danger:hover {
		background: rgba(255, 237, 237, 0.96);
		color: #b35a5a;
	}

	.dochat-documents-loading,
	.dochat-documents-empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.6rem;
		padding: 2rem 1rem;
		text-align: center;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-documents-empty p,
	.dochat-documents-helper {
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-documents-helper {
		padding-inline: 0.35rem;
		font-size: 0.82rem;
	}

	.dochat-documents-helper code {
		padding: 0.15rem 0.35rem;
		border-radius: 0.45rem;
		background: rgba(255, 255, 255, 0.9);
	}

	.dochat-metadata-modal-backdrop {
		position: fixed;
		inset: 0;
		z-index: 40;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: 1rem;
		background: rgba(29, 29, 31, 0.16);
		backdrop-filter: blur(8px);
	}

	.dochat-metadata-modal {
		width: min(56rem, 100%);
		max-height: min(92dvh, 52rem);
		overflow: auto;
		padding: 1.2rem;
		border-radius: 1.4rem;
		border: 1px solid rgba(221, 214, 202, 0.9);
		background: rgba(255, 255, 255, 0.98);
		box-shadow: 0 24px 64px rgba(84, 74, 58, 0.14);
	}

	.dochat-metadata-modal-head {
		display: flex;
		justify-content: space-between;
		gap: 1rem;
		align-items: start;
	}

	.dochat-metadata-modal h2 {
		font-size: 1.25rem;
		font-weight: 700;
		margin-top: 0.35rem;
	}

	.dochat-metadata-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.65rem;
		margin-top: 1rem;
	}

	.dochat-documents-primary-soft {
		background: rgba(232, 239, 228, 0.96);
		color: var(--dochat-accent, #6f8a64);
	}

	.dochat-metadata-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 0.85rem;
		margin-top: 1rem;
	}

	.dochat-metadata-field {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}

	.dochat-metadata-field-wide {
		grid-column: 1 / -1;
	}

	.dochat-metadata-field span {
		font-size: 0.76rem;
		font-weight: 700;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-metadata-field input,
	.dochat-metadata-field textarea,
	.dochat-metadata-field select {
		width: 100%;
		border: 1px solid rgba(221, 214, 202, 0.96);
		border-radius: 1rem;
		padding: 0.85rem 0.95rem;
		background: rgba(251, 249, 245, 0.94);
		color: var(--dochat-text, #1d1d1f);
		outline: none;
	}

	.dochat-metadata-field textarea {
		min-height: 6rem;
		resize: vertical;
	}

	.dochat-metadata-field input:focus,
	.dochat-metadata-field textarea:focus,
	.dochat-metadata-field select:focus {
		border-color: rgba(111, 138, 100, 0.55);
		box-shadow: 0 0 0 3px rgba(111, 138, 100, 0.14);
	}

	@media (max-width: 1100px) {
		.dochat-document-grid {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}

	@media (max-width: 767px) {
		.dochat-documents-hero,
		.dochat-documents-toolbar {
			flex-direction: column;
			align-items: stretch;
		}

		.dochat-documents-summary,
		.dochat-document-grid,
		.dochat-collection-meta,
		.dochat-metadata-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
