<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import dayjs from '$lib/dayjs';
	import localizedFormat from 'dayjs/plugin/localizedFormat';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import { getChatListBySearchText } from '$lib/apis/chats';
	import { searchKnowledgeFiles } from '$lib/apis/knowledge';
	import { searchNotes as searchLegacyNotes } from '$lib/apis/notes';
	import { searchWorkspace } from '$lib/apis/search';
	import { WEBUI_NAME } from '$lib/stores';

	import Search from '$lib/components/icons/Search.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import HighlightedText from '$lib/components/search/HighlightedText.svelte';

	dayjs.extend(localizedFormat);
	dayjs.extend(relativeTime);

	type SearchOccurrence = {
		id: string;
		snippet: string;
		matched_text: string;
		location?: string | null;
		page?: number | null;
		message_id?: string | null;
		start?: number | null;
		end?: number | null;
		updated_at?: number | null;
		is_archived?: boolean | null;
	};

	type SearchCollection = {
		id?: string;
		name?: string;
	};

	type SearchMetadata = {
		archived?: boolean;
		pinned?: boolean;
		source?: string;
		content_type?: string;
		fallback?: boolean;
		[key: string]: unknown;
	};

	type SearchMessageDetail = {
		id?: string;
		role?: string;
		content?: string;
		created_at?: number | null;
	};

	type SearchDetail = {
		message_count?: number;
		messages?: SearchMessageDetail[];
		last_sources?: unknown[];
		document_context?: Record<string, unknown>;
		content_md?: string;
		linked_collections?: SearchCollection[];
		meta?: Record<string, unknown>;
		primary_collection?: SearchCollection | null;
		collection?: SearchCollection | null;
		source?: string;
		description?: string;
		summary?: string;
		processing_status?: string;
		author?: string;
		language?: string;
		document_type?: string;
		[key: string]: unknown;
	};

	type SearchResult = {
		id: string;
		type: 'chat' | 'note' | 'document';
		title: string;
		snippet?: string | null;
		score?: number;
		updated_at?: number | null;
		collection?: SearchCollection | null;
		tags?: string[];
		metadata?: SearchMetadata;
		detail?: SearchDetail;
		occurrence_count?: number;
		occurrences?: SearchOccurrence[];
	};

	let loaded = false;
	let loading = false;
	let query = '';
	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	let typeFilter = 'all';
	let archiveFilter = 'all';
	let dateFilter = 'all';
	let collectionFilter = 'all';
	let sourceFilter = 'all';
	let tagFilter = 'all';

	let resultGroups: SearchResult[] = [];
	let total = 0;
	let selectedResultId: string | null = null;
	let selectedOccurrenceId: string | null = null;
	let expandedResultIds: string[] = [];

	const typeOptions = [
		{ value: 'all', label: 'Tudo' },
		{ value: 'chat', label: 'Chats' },
		{ value: 'note', label: 'Notas' },
		{ value: 'document', label: 'Documentos' }
	];

	const archiveOptions = [
		{ value: 'all', label: 'Ativos + arquivados' },
		{ value: 'active', label: 'Somente ativos' },
		{ value: 'archived', label: 'Somente arquivados' }
	];

	const dateOptions = [
		{ value: 'all', label: 'Qualquer data' },
		{ value: '7d', label: 'Ultimos 7 dias' },
		{ value: '30d', label: 'Ultimos 30 dias' },
		{ value: '90d', label: 'Ultimos 90 dias' }
	];

	const getActiveQuery = () => query.trim();
	const getOccurrenceTerm = (occurrence: SearchOccurrence | null | undefined) =>
		occurrence?.matched_text || getActiveQuery();

	const updateUrl = () => {
		if (typeof window === 'undefined') return;

		const search = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : '';
		window.history.replaceState(null, '', `${$page.url.pathname}${search}`);
	};

	const getDateRange = () => {
		if (dateFilter === 'all') {
			return { date_from: null, date_to: null };
		}

		const now = dayjs();
		if (dateFilter === '7d') {
			return { date_from: now.subtract(7, 'day').unix(), date_to: now.unix() };
		}
		if (dateFilter === '30d') {
			return { date_from: now.subtract(30, 'day').unix(), date_to: now.unix() };
		}
		if (dateFilter === '90d') {
			return { date_from: now.subtract(90, 'day').unix(), date_to: now.unix() };
		}

		return { date_from: null, date_to: null };
	};

	const toEpochSeconds = (value: number | null | undefined) => {
		if (value === null || value === undefined) return null;
		return value > 10_000_000_000 ? Math.floor(value / 1_000_000_000) : value;
	};

	const buildFallbackOccurrences = (
		prefix: string,
		snippet: string,
		updatedAt: number | null = null,
		location = 'resultado'
	) =>
		query.trim()
			? [
					{
						id: `${prefix}:fallback`,
						snippet,
						matched_text: query.trim(),
						location,
						updated_at: updatedAt
					}
				]
			: [];

	const loadFallbackResults = async () => {
		const normalizedQuery = query.trim();
		if (!normalizedQuery) {
			return { items: [], total: 0 };
		}

		const [chatItems, noteRes, documentRes] = await Promise.all([
			typeFilter === 'all' || typeFilter === 'chat'
				? getChatListBySearchText(localStorage.token, normalizedQuery, 1).catch(() => [])
				: Promise.resolve([]),
			typeFilter === 'all' || typeFilter === 'note'
				? searchLegacyNotes(localStorage.token, normalizedQuery, null, null, null, 1).catch(
						() => null
					)
				: Promise.resolve(null),
			typeFilter === 'all' || typeFilter === 'document'
				? searchKnowledgeFiles(localStorage.token, normalizedQuery, null, null, null, 1).catch(
						() => null
					)
				: Promise.resolve(null)
		]);

		const fallbackItems: SearchResult[] = [];

		for (const chat of chatItems ?? []) {
			const updatedAt = toEpochSeconds(chat.updated_at);
			const snippet = chat.title || 'Conversa encontrada';
			const occurrences = buildFallbackOccurrences(`chat:${chat.id}`, snippet, updatedAt, 'chat');
			fallbackItems.push({
				id: chat.id,
				type: 'chat',
				title: chat.title || 'Sem titulo',
				snippet,
				updated_at: updatedAt,
				metadata: {
					archived: chat.archived ?? false,
					pinned: chat.pinned ?? false,
					fallback: true
				},
				detail: {},
				occurrence_count: occurrences.length,
				occurrences
			});
		}

		for (const note of noteRes?.items ?? []) {
			const updatedAt = toEpochSeconds(note.updated_at);
			const snippet = note?.data?.content?.md?.slice(0, 280) || note.title || 'Nota encontrada';
			const occurrences = buildFallbackOccurrences(`note:${note.id}`, snippet, updatedAt, 'nota');
			fallbackItems.push({
				id: note.id,
				type: 'note',
				title: note.title || 'Sem titulo',
				snippet,
				updated_at: updatedAt,
				metadata: {
					archived: note.is_archived ?? false,
					pinned: note.is_pinned ?? false,
					fallback: true
				},
				detail: {
					content_md: note?.data?.content?.md || ''
				},
				occurrence_count: occurrences.length,
				occurrences
			});
		}

		for (const file of documentRes?.items ?? []) {
			const updatedAt = toEpochSeconds(file.updated_at);
			const snippet =
				file.description ||
				file.meta?.description ||
				file.meta?.name ||
				file.filename ||
				'Documento';
			const occurrences = buildFallbackOccurrences(
				`document:${file.id}`,
				snippet,
				updatedAt,
				'documento'
			);
			fallbackItems.push({
				id: file.id,
				type: 'document',
				title: file.meta?.name || file.filename || 'Documento',
				snippet,
				updated_at: updatedAt,
				metadata: {
					source: file.meta?.source || file.filename,
					content_type: file.meta?.content_type,
					fallback: true
				},
				detail: {
					source: file.meta?.source || file.filename,
					description: snippet,
					processing_status: file.data?.processing_status || file.data?.status || 'ready'
				},
				occurrence_count: occurrences.length,
				occurrences
			});
		}

		fallbackItems.sort((a, b) => (b.updated_at ?? 0) - (a.updated_at ?? 0));
		return { items: fallbackItems, total: fallbackItems.length };
	};

	const hasActiveSearchState = () =>
		Boolean(
			query.trim() ||
			typeFilter !== 'all' ||
			archiveFilter !== 'all' ||
			dateFilter !== 'all' ||
			collectionFilter !== 'all' ||
			sourceFilter !== 'all' ||
			tagFilter !== 'all'
		);

	const loadResults = async () => {
		if (!loaded) return;

		loading = true;
		let res = null;

		const { date_from, date_to } = getDateRange();
		res = await searchWorkspace(localStorage.token, {
			query: query.trim() || null,
			type: typeFilter === 'all' ? null : typeFilter,
			archived: archiveFilter === 'all' ? null : archiveFilter === 'archived' ? true : false,
			date_from,
			date_to,
			collection: collectionFilter === 'all' ? null : collectionFilter,
			source: sourceFilter === 'all' ? null : sourceFilter,
			tags: tagFilter === 'all' ? null : [tagFilter],
			page: 1,
			limit: 200
		}).catch(async () => {
			if (!hasActiveSearchState()) {
				return { items: [], total: 0 };
			}

			return await loadFallbackResults().catch(() => null);
		});

		resultGroups = res?.items ?? [];
		total = res?.total ?? resultGroups.length;

		if (resultGroups.length === 0) {
			selectedResultId = null;
			selectedOccurrenceId = null;
			expandedResultIds = [];
		} else {
			if (!resultGroups.some((item) => item.id === selectedResultId)) {
				selectedResultId = resultGroups[0].id;
			}

			const selected = resultGroups.find((item) => item.id === selectedResultId);
			if (selected && !expandedResultIds.includes(selected.id)) {
				expandedResultIds = [...expandedResultIds, selected.id];
			}

			if (
				selected &&
				selected.occurrences?.length &&
				!selected.occurrences.some((occurrence) => occurrence.id === selectedOccurrenceId)
			) {
				selectedOccurrenceId = selected.occurrences[0].id;
			}
		}

		updateUrl();
		loading = false;
	};

	const toggleExpanded = (resultId: string) => {
		expandedResultIds = expandedResultIds.includes(resultId)
			? expandedResultIds.filter((id) => id !== resultId)
			: [...expandedResultIds, resultId];
	};

	const selectResult = (result: SearchResult, occurrenceId: string | null = null) => {
		selectedResultId = result.id;
		if (!expandedResultIds.includes(result.id)) {
			expandedResultIds = [...expandedResultIds, result.id];
		}
		selectedOccurrenceId = occurrenceId ?? result.occurrences?.[0]?.id ?? null;
	};

	const openResult = async (result: SearchResult) => {
		if (result.type === 'chat') {
			await goto(`/c/${result.id}`);
			return;
		}

		if (result.type === 'note') {
			await goto(`/notes/${result.id}`);
			return;
		}

		const collectionId = result.collection?.id ?? result.detail?.primary_collection?.id;
		if (collectionId) {
			await goto(`/workspace/knowledge/${collectionId}`);
			return;
		}

		await goto('/workspace/knowledge');
	};

	const openOccurrence = async (result: SearchResult, occurrence: SearchOccurrence) => {
		selectResult(result, occurrence.id);
		await openResult(result);
	};

	const getResultLabel = (type: SearchResult['type']) => {
		if (type === 'chat') return 'Chat';
		if (type === 'note') return 'Nota';
		return 'Documento';
	};

	const getResultMetaValue = (result: SearchResult) => {
		if (result.type === 'chat') {
			return result.metadata?.archived ? 'Arquivado' : 'Conversa';
		}
		if (result.type === 'note') {
			if (result.metadata?.pinned) return 'Fixada';
			return result.metadata?.archived ? 'Arquivada' : 'Nota';
		}
		return result.detail?.document_type || result.metadata?.content_type || 'Documento';
	};

	const normalizeTags = (result: SearchResult) => (result.tags ?? []).filter(Boolean);
	const getCollections = () =>
		[
			...new Set(
				resultGroups
					.map((item) => item.collection?.name || item.detail?.collection?.name || '')
					.filter(Boolean)
			)
		].sort((a, b) => a.localeCompare(b));
	const getSources = () =>
		[
			...new Set(
				resultGroups
					.map((item) => item.detail?.source || item.metadata?.source || '')
					.filter(Boolean)
			)
		].sort((a, b) => a.localeCompare(b));
	const getTags = () =>
		[...new Set(resultGroups.flatMap((item) => normalizeTags(item)).filter(Boolean))].sort((a, b) =>
			a.localeCompare(b)
		);

	$: availableCollections = getCollections();
	$: availableSources = getSources();
	$: availableTags = getTags();
	$: selectedResult = resultGroups.find((item) => item.id === selectedResultId) ?? null;
	$: selectedOccurrence =
		selectedResult?.occurrences?.find((item) => item.id === selectedOccurrenceId) ??
		selectedResult?.occurrences?.[0] ??
		null;
	$: searchSignature = [
		loaded ? '1' : '0',
		query,
		typeFilter,
		archiveFilter,
		dateFilter,
		collectionFilter,
		sourceFilter,
		tagFilter
	].join('::');

	$: if (loaded && searchSignature) {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(async () => {
			await loadResults();
		}, 240);
	}

	onMount(async () => {
		query = $page.url.searchParams.get('q') ?? '';
		loaded = true;
		await loadResults();
	});

	onDestroy(() => {
		clearTimeout(searchDebounceTimer);
	});
</script>

<svelte:head>
	<title>Pesquisa tranversal • {$WEBUI_NAME}</title>
</svelte:head>

<div class="dochat-search-page">
	<section class="dochat-search-hero">
		<div>
			<div class="dochat-search-kicker">Pesquisa tranversal</div>
			<h1>Pesquisa tranversal</h1>
			<p>
				Procure palavras ou termos em chats, notas, documentos transcritos em markdown e
				metadados, com contexto suficiente para seguir direto para o ponto certo.
			</p>
		</div>

		<div class="dochat-search-input">
			<Search className="size-4" />
			<input
				bind:value={query}
				placeholder="Pesquisar por palavras ou termos em todo o Dochat"
				aria-label="Pesquisa tranversal"
			/>

			{#if query}
				<button
					type="button"
					class="dochat-search-clear"
					aria-label="Limpar busca"
					on:click={() => {
						query = '';
					}}
				>
					<XMark className="size-3.5" strokeWidth="2" />
				</button>
			{/if}
		</div>
	</section>

	<section class="dochat-search-summary">
		<div class="dochat-search-summary-card">
			<span>Resultados</span>
			<strong>{total}</strong>
		</div>
		<div class="dochat-search-summary-card">
			<span>Filtro</span>
			<strong
				>{archiveOptions.find((item) => item.value === archiveFilter)?.label ?? 'Todos'}</strong
			>
		</div>
		<div class="dochat-search-summary-card">
			<span>Modo</span>
			<strong>{query ? 'Ocorrencias' : 'Exploracao'}</strong>
		</div>
	</section>

	<div class="dochat-search-grid">
		<aside class="dochat-search-panel dochat-search-filters">
			<div class="dochat-search-panel-head">
				<h2>Filtros</h2>
				<p>Refine por tipo, arquivo e origem.</p>
			</div>

			<label class="dochat-search-filter">
				<span>Tipo</span>
				<select bind:value={typeFilter}>
					{#each typeOptions as option}
						<option value={option.value}>{option.label}</option>
					{/each}
				</select>
			</label>

			<label class="dochat-search-filter">
				<span>Arquivo</span>
				<select bind:value={archiveFilter}>
					{#each archiveOptions as option}
						<option value={option.value}>{option.label}</option>
					{/each}
				</select>
			</label>

			<label class="dochat-search-filter">
				<span>Periodo</span>
				<select bind:value={dateFilter}>
					{#each dateOptions as option}
						<option value={option.value}>{option.label}</option>
					{/each}
				</select>
			</label>

			<label class="dochat-search-filter">
				<span>Colecao</span>
				<select bind:value={collectionFilter}>
					<option value="all">Todas</option>
					{#each availableCollections as collection}
						<option value={collection}>{collection}</option>
					{/each}
				</select>
			</label>

			<label class="dochat-search-filter">
				<span>Origem</span>
				<select bind:value={sourceFilter}>
					<option value="all">Todas</option>
					{#each availableSources as source}
						<option value={source}>{source}</option>
					{/each}
				</select>
			</label>

			<label class="dochat-search-filter">
				<span>Tag</span>
				<select bind:value={tagFilter}>
					<option value="all">Todas</option>
					{#each availableTags as tag}
						<option value={tag}>{tag}</option>
					{/each}
				</select>
			</label>
		</aside>

		<section class="dochat-search-panel dochat-search-results">
			<div class="dochat-search-panel-head">
				<h2>Ocorrencias</h2>
				<p>
					{query
						? 'Cada grupo reune um recurso com todas as mencoes encontradas.'
						: 'Use a pesquisa tranversal para abrir contexto, notas e documentos rapidamente.'}
				</p>
			</div>

			{#if loading}
				<div class="dochat-search-empty">
					<Spinner className="size-5" />
				</div>
			{:else if resultGroups.length === 0}
				<div class="dochat-search-empty">
					<div>Nenhum resultado encontrado.</div>
				</div>
			{:else}
				<div class="dochat-search-result-list">
					{#each resultGroups as result}
						<div class:selected={selectedResultId === result.id} class="dochat-search-result-card">
							<button
								type="button"
								class="dochat-search-result-button"
								on:click={() => selectResult(result)}
							>
								<div class="dochat-search-result-head">
									<div class="dochat-search-result-title">
										<HighlightedText text={result.title || 'Sem titulo'} term={getActiveQuery()} />
									</div>
									<div class="dochat-search-result-badges">
										<span class="dochat-search-type">{getResultLabel(result.type)}</span>
										{#if result.metadata?.archived}
											<span class="dochat-search-archive-chip">Arquivado</span>
										{/if}
									</div>
								</div>

								<div class="dochat-search-result-preview">
									<HighlightedText
										text={result.snippet || 'Sem previa disponivel.'}
										term={getActiveQuery()}
									/>
								</div>

								<div class="dochat-search-result-meta">
									<span>{getResultMetaValue(result)}</span>
									{#if result.collection?.name}
										<span>{result.collection.name}</span>
									{/if}
									{#if result.updated_at}
										<Tooltip content={dayjs.unix(result.updated_at).format('LLLL')}>
											<span>{dayjs.unix(result.updated_at).fromNow()}</span>
										</Tooltip>
									{/if}
									<span class="dochat-search-occurrence-count">
										{result.occurrence_count ?? 0} ocorrencias
									</span>
								</div>
							</button>

							{#if result.occurrences?.length}
								<div class="dochat-search-occurrences">
									{#each expandedResultIds.includes(result.id) ? result.occurrences : result.occurrences.slice(0, 3) as occurrence}
										<button
											type="button"
											class:selected={selectedOccurrenceId === occurrence.id}
											class="dochat-search-occurrence"
											on:click={() => selectResult(result, occurrence.id)}
											on:dblclick={() => openOccurrence(result, occurrence)}
										>
											<div class="dochat-search-occurrence-head">
												<span>{occurrence.location || 'Ocorrencia'}</span>
												{#if occurrence.page}
													<span>Pagina {occurrence.page}</span>
												{/if}
											</div>
											<div>
												<HighlightedText
													text={occurrence.snippet}
													term={getOccurrenceTerm(occurrence)}
												/>
											</div>
										</button>
									{/each}

									{#if result.occurrences.length > 3}
										<button
											type="button"
											class="dochat-search-expand"
											on:click={() => toggleExpanded(result.id)}
										>
											{#if expandedResultIds.includes(result.id)}
												Mostrar menos
											{:else}
												Mostrar todas
											{/if}
										</button>
									{/if}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</section>

		<aside class="dochat-search-panel dochat-search-detail">
			<div class="dochat-search-panel-head">
				<h2>Detalhe</h2>
				<p>Abra o recurso original ou revise todas as mencoes no painel.</p>
			</div>

			{#if selectedResult}
				<div class="dochat-search-detail-card">
					<div class="dochat-search-detail-head">
						<div>
							<div class="dochat-search-detail-title">
								<HighlightedText text={selectedResult.title} term={getActiveQuery()} />
							</div>
							<div class="dochat-search-detail-subtitle">
								{getResultLabel(selectedResult.type)}
								{#if selectedResult.collection?.name}
									• {selectedResult.collection.name}
								{/if}
							</div>
						</div>

						<button
							type="button"
							class="dochat-search-open"
							on:click={() => openResult(selectedResult)}
						>
							Abrir item
						</button>
					</div>

					{#if selectedOccurrence}
						<div class="dochat-search-detail-body">
							<div class="dochat-search-detail-section">Ocorrencia em destaque</div>
							<div
								class="dochat-search-detail-occurrence dochat-search-detail-occurrence-focus selected"
							>
								<div class="dochat-search-occurrence-head">
									<span>{selectedOccurrence.location || 'Ocorrencia'}</span>
									{#if selectedOccurrence.page}
										<span>Pagina {selectedOccurrence.page}</span>
									{/if}
								</div>
								<div>
									<HighlightedText
										text={selectedOccurrence.snippet}
										term={getOccurrenceTerm(selectedOccurrence)}
									/>
								</div>
							</div>
						</div>
					{/if}

					{#if selectedResult.type === 'chat'}
						<div class="dochat-search-detail-body">
							{#each selectedResult.detail?.messages ?? [] as message}
								<div class="dochat-search-message-row">
									<strong>{message.role === 'assistant' ? 'Assistente' : 'Voce'}</strong>
									<span><HighlightedText text={message.content} term={getActiveQuery()} /></span>
								</div>
							{/each}
						</div>
					{:else if selectedResult.type === 'note'}
						<div class="dochat-search-detail-body dochat-search-detail-text">
							<HighlightedText
								text={selectedResult.detail?.content_md ||
									selectedResult.snippet ||
									'Sem conteudo detalhado.'}
								term={getActiveQuery()}
							/>
						</div>
					{:else}
						<div class="dochat-search-detail-body">
							<div class="dochat-search-detail-text">
								<HighlightedText
									text={selectedOccurrence?.snippet ||
										selectedResult.detail?.description ||
										selectedResult.detail?.summary ||
										selectedResult.snippet ||
										'Documento pronto para consulta.'}
									term={selectedOccurrence
										? getOccurrenceTerm(selectedOccurrence)
										: getActiveQuery()}
								/>
							</div>

							<div class="dochat-search-detail-metadata">
								{#if selectedResult.detail?.source}
									<div><span>Origem</span><strong>{selectedResult.detail.source}</strong></div>
								{/if}
								{#if selectedResult.detail?.author}
									<div><span>Autor</span><strong>{selectedResult.detail.author}</strong></div>
								{/if}
								{#if selectedResult.detail?.language}
									<div><span>Idioma</span><strong>{selectedResult.detail.language}</strong></div>
								{/if}
								<div>
									<span>Status</span>
									<strong>{selectedResult.detail?.processing_status || 'ready'}</strong>
								</div>
							</div>
						</div>
					{/if}

					{#if selectedResult.occurrences?.length}
						<div class="dochat-search-detail-occurrences">
							<div class="dochat-search-detail-section">Ocorrencias</div>
							{#each selectedResult.occurrences as occurrence}
								<button
									type="button"
									class:selected={selectedOccurrence?.id === occurrence.id}
									class="dochat-search-detail-occurrence"
									on:click={() => {
										selectedOccurrenceId = occurrence.id;
									}}
								>
									<div class="dochat-search-occurrence-head">
										<span>{occurrence.location || 'Ocorrencia'}</span>
										{#if occurrence.page}
											<span>Pagina {occurrence.page}</span>
										{/if}
									</div>
									<div>
										<HighlightedText
											text={occurrence.snippet}
											term={getOccurrenceTerm(occurrence)}
										/>
									</div>
								</button>
							{/each}
						</div>
					{/if}
				</div>
			{:else}
				<div class="dochat-search-empty">
					<div>Selecione um resultado para ver o detalhe.</div>
				</div>
			{/if}
		</aside>
	</div>
</div>

<style>
	.dochat-search-page {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		width: 100%;
		min-width: 0;
		height: 100%;
		padding: 1rem 1.1rem 1.25rem;
		overflow: hidden;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-search-hero {
		display: flex;
		justify-content: space-between;
		gap: 1rem;
		align-items: end;
		padding: 1.2rem 1.3rem;
		border: 1px solid rgba(221, 214, 202, 0.88);
		border-radius: 1.55rem;
		background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(251, 249, 245, 0.94));
		box-shadow: 0 18px 40px rgba(84, 74, 58, 0.06);
	}

	.dochat-search-kicker {
		font-size: 0.72rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--dochat-accent, #6f8a64);
	}

	h1 {
		font-size: clamp(1.55rem, 2vw, 2.1rem);
		font-weight: 700;
		line-height: 1.05;
		margin: 0.3rem 0;
	}

	p {
		margin: 0;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-search-input {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		min-width: min(100%, 24rem);
		padding: 0.9rem 1rem;
		border: 1px solid rgba(221, 214, 202, 0.9);
		border-radius: 1.15rem;
		background: rgba(255, 255, 255, 0.98);
		color: var(--dochat-accent, #6f8a64);
	}

	.dochat-search-input input {
		flex: 1;
		min-width: 0;
		background: transparent;
		outline: none;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-search-input input::placeholder {
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-search-clear {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-search-clear:hover {
		color: var(--dochat-accent, #6f8a64);
	}

	.dochat-search-summary {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 0.85rem;
	}

	.dochat-search-summary-card,
	.dochat-search-panel {
		border: 1px solid rgba(231, 225, 216, 0.96);
		border-radius: 1.35rem;
		background: rgba(255, 255, 255, 0.92);
		box-shadow: 0 14px 28px rgba(84, 74, 58, 0.04);
	}

	.dochat-search-summary-card {
		padding: 1rem 1.1rem;
	}

	.dochat-search-summary-card span {
		display: block;
		font-size: 0.75rem;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-search-summary-card strong {
		display: block;
		margin-top: 0.25rem;
		font-size: 1.15rem;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-search-grid {
		flex: 1;
		min-height: 0;
		display: grid;
		grid-template-columns: minmax(16rem, 19rem) minmax(0, 1.15fr) minmax(20rem, 0.95fr);
		gap: 0.95rem;
	}

	.dochat-search-panel {
		min-width: 0;
		min-height: 0;
		display: flex;
		flex-direction: column;
		overflow: hidden;
	}

	.dochat-search-panel-head {
		padding: 1rem 1.05rem 0.85rem;
		border-bottom: 1px solid rgba(231, 225, 216, 0.82);
	}

	.dochat-search-panel-head h2 {
		margin: 0;
		font-size: 0.96rem;
		font-weight: 700;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-search-panel-head p {
		margin-top: 0.35rem;
		font-size: 0.84rem;
	}

	.dochat-search-filters {
		padding-bottom: 1rem;
	}

	.dochat-search-filter {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		padding: 0.9rem 1rem 0;
	}

	.dochat-search-filter span {
		font-size: 0.78rem;
		font-weight: 600;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-search-filter select {
		border: 1px solid rgba(221, 214, 202, 0.96);
		border-radius: 0.95rem;
		padding: 0.8rem 0.9rem;
		background: rgba(251, 249, 245, 0.94);
		color: var(--dochat-text, #1d1d1f);
		outline: none;
	}

	.dochat-search-filter select:focus {
		border-color: rgba(111, 138, 100, 0.55);
		box-shadow: 0 0 0 3px rgba(111, 138, 100, 0.14);
	}

	.dochat-search-results,
	.dochat-search-detail {
		overflow: hidden;
	}

	.dochat-search-result-list,
	.dochat-search-detail-card,
	.dochat-search-empty {
		flex: 1;
		min-height: 0;
		overflow: auto;
		padding: 0.85rem;
	}

	.dochat-search-result-list {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.dochat-search-result-card {
		border: 1px solid rgba(231, 225, 216, 0.96);
		border-radius: 1.1rem;
		background: rgba(251, 249, 245, 0.86);
		transition:
			border-color 180ms ease,
			box-shadow 180ms ease,
			background 180ms ease;
	}

	.dochat-search-result-card.selected {
		border-color: rgba(111, 138, 100, 0.42);
		background: rgba(232, 239, 228, 0.7);
		box-shadow: 0 12px 24px rgba(111, 138, 100, 0.08);
	}

	.dochat-search-result-button {
		width: 100%;
		padding: 0.95rem;
		text-align: left;
	}

	.dochat-search-result-head {
		display: flex;
		justify-content: space-between;
		gap: 0.75rem;
		align-items: start;
	}

	.dochat-search-result-title,
	.dochat-search-detail-title {
		font-size: 0.98rem;
		font-weight: 700;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-search-result-badges {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
		justify-content: end;
	}

	.dochat-search-type,
	.dochat-search-archive-chip {
		padding: 0.22rem 0.55rem;
		border-radius: 999px;
		font-size: 0.72rem;
		font-weight: 600;
	}

	.dochat-search-type {
		background: rgba(232, 239, 228, 0.88);
		color: var(--dochat-accent, #6f8a64);
	}

	.dochat-search-archive-chip {
		background: rgba(247, 244, 238, 0.98);
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-search-result-preview,
	.dochat-search-detail-text {
		margin-top: 0.55rem;
		font-size: 0.9rem;
		line-height: 1.55;
		color: var(--dochat-text-soft, #5c5c62);
	}

	:global(.dochat-search-highlight) {
		padding: 0.02rem 0.22rem;
		border-radius: 0.32rem;
		background: rgba(239, 223, 141, 0.78);
		color: inherit;
		box-shadow: inset 0 0 0 1px rgba(196, 156, 37, 0.18);
	}

	.dochat-search-result-meta,
	.dochat-search-occurrence-head,
	.dochat-search-detail-subtitle {
		display: flex;
		flex-wrap: wrap;
		gap: 0.55rem;
		margin-top: 0.65rem;
		font-size: 0.76rem;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-search-occurrence-count {
		color: var(--dochat-accent, #6f8a64);
		font-weight: 600;
	}

	.dochat-search-occurrences {
		display: flex;
		flex-direction: column;
		gap: 0.45rem;
		padding: 0 0.95rem 0.9rem;
	}

	.dochat-search-occurrence,
	.dochat-search-detail-occurrence {
		width: 100%;
		padding: 0.72rem 0.8rem;
		text-align: left;
		border-radius: 0.95rem;
		border: 1px solid rgba(231, 225, 216, 0.96);
		background: rgba(255, 255, 255, 0.8);
		font-size: 0.84rem;
		line-height: 1.5;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-search-occurrence.selected,
	.dochat-search-detail-occurrence.selected {
		border-color: rgba(111, 138, 100, 0.38);
		background: rgba(232, 239, 228, 0.72);
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-search-detail-occurrence-focus {
		margin-top: 0;
	}

	.dochat-search-expand,
	.dochat-search-open {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 0.58rem 0.9rem;
		border-radius: 999px;
		background: rgba(232, 239, 228, 0.86);
		color: var(--dochat-accent, #6f8a64);
		font-size: 0.8rem;
		font-weight: 700;
	}

	.dochat-search-expand:hover,
	.dochat-search-open:hover {
		background: rgba(111, 138, 100, 0.16);
	}

	.dochat-search-detail-head {
		display: flex;
		justify-content: space-between;
		align-items: start;
		gap: 0.9rem;
	}

	.dochat-search-detail-body {
		display: flex;
		flex-direction: column;
		gap: 0.85rem;
		margin-top: 1rem;
	}

	.dochat-search-message-row {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		padding: 0.8rem 0.9rem;
		border-radius: 1rem;
		background: rgba(251, 249, 245, 0.92);
		border: 1px solid rgba(231, 225, 216, 0.9);
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-search-detail-metadata {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 0.7rem;
	}

	.dochat-search-detail-metadata div {
		padding: 0.78rem 0.85rem;
		border-radius: 1rem;
		background: rgba(251, 249, 245, 0.92);
		border: 1px solid rgba(231, 225, 216, 0.9);
	}

	.dochat-search-detail-metadata span {
		display: block;
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-search-detail-metadata strong {
		display: block;
		margin-top: 0.22rem;
		font-size: 0.9rem;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-search-detail-occurrences {
		margin-top: 1rem;
		display: flex;
		flex-direction: column;
		gap: 0.55rem;
	}

	.dochat-search-detail-section {
		font-size: 0.76rem;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--dochat-accent, #6f8a64);
	}

	.dochat-search-empty {
		display: flex;
		align-items: center;
		justify-content: center;
		color: var(--dochat-text-soft, #5c5c62);
	}

	@media (max-width: 1100px) {
		.dochat-search-grid {
			grid-template-columns: 1fr;
			overflow: auto;
		}

		.dochat-search-summary {
			grid-template-columns: 1fr;
		}

		.dochat-search-hero {
			flex-direction: column;
			align-items: stretch;
		}

		.dochat-search-input {
			min-width: 0;
		}
	}
</style>
