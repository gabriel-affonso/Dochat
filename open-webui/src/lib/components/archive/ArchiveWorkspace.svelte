<script lang="ts">
	import fileSaver from 'file-saver';
	import { toast } from 'svelte-sonner';
	import { onDestroy, onMount } from 'svelte';
	import { goto } from '$app/navigation';

	import dayjs from '$lib/dayjs';
	import localizedFormat from 'dayjs/plugin/localizedFormat';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(localizedFormat);
	dayjs.extend(relativeTime);

	import {
		archiveChatById,
		getAllArchivedChats,
		getArchivedChatList,
		getPinnedChatList,
		unarchiveAllChats
	} from '$lib/apis/chats';
	import { getNotes } from '$lib/apis/notes';
	import { searchKnowledgeFiles } from '$lib/apis/knowledge';
	import { WEBUI_NAME, config, user } from '$lib/stores';
	import { formatFileSize } from '$lib/utils';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Search from '$lib/components/icons/Search.svelte';
	import ArchiveBox from '$lib/components/icons/ArchiveBox.svelte';
	import ChatBubbles from '$lib/components/icons/ChatBubbles.svelte';
	import Note from '$lib/components/icons/Note.svelte';
	import Document from '$lib/components/icons/Document.svelte';

	const { saveAs } = fileSaver;

	let loaded = false;
	let loading = false;
	let exporting = false;
	let restoringAll = false;

	let query = '';
	let archiveWindowDays = 30;
	let refreshTimer: ReturnType<typeof setTimeout>;

	let archivedChats = [];
	let pinnedChatItems = [];
	let allNotes = [];
	let candidateDocuments = [];

	const canAccessNotes = () =>
		($config?.features?.enable_notes ?? false) &&
		($user?.role === 'admin' || ($user?.permissions?.features?.notes ?? true));

	const canAccessDocuments = () =>
		$user?.role === 'admin' || Boolean($user?.permissions?.workspace?.knowledge);

	const normalizeText = (value: string | null | undefined) => (value ?? '').toLowerCase().trim();

	const matchesQuery = (...values: Array<string | null | undefined>) => {
		const currentQuery = normalizeText(query);
		if (!currentQuery) return true;

		return values.some((value) => normalizeText(value).includes(currentQuery));
	};

	const isOlderThan = (timestamp: number | null, days: number) => {
		if (!timestamp) return false;
		return Date.now() - timestamp >= days * 24 * 60 * 60 * 1000;
	};

	const getChatTitle = (chat) => chat?.title || 'Conversa sem titulo';
	const getNoteTitle = (note) => note?.title || 'Nota sem titulo';
	const getDocumentTitle = (document) =>
		document?.meta?.name || document?.filename || 'Documento';

	const getNoteTimestamp = (note) => (note?.updated_at ? note.updated_at / 1000000 : null);
	const getDocumentTimestamp = (document) => (document?.updated_at ? document.updated_at * 1000 : null);
	const getChatTimestamp = (chat) => (chat?.updated_at ? chat.updated_at * 1000 : null);

	const getNotePreview = (note) =>
		(note?.data?.content?.md ?? 'Sem conteudo')
			.replace(/\s+/g, ' ')
			.trim()
			.slice(0, 220);

	const getDocumentPreview = (document) =>
		(document?.description || document?.data?.content || 'Documento indexado na biblioteca.')
			.replace(/\s+/g, ' ')
			.trim()
			.slice(0, 220);

	const loadArchiveData = async () => {
		loading = true;

		const filter = query.trim()
			? {
					query: query.trim(),
					order_by: 'updated_at',
					direction: 'desc'
				}
			: {
					order_by: 'updated_at',
					direction: 'desc'
				};

		const [archivedRes, pinnedRes, notesRes, documentsRes] = await Promise.all([
			getArchivedChatList(localStorage.token, 1, filter).catch(() => []),
			getPinnedChatList(localStorage.token).catch(() => []),
			canAccessNotes() ? getNotes(localStorage.token, true).catch(() => []) : Promise.resolve([]),
			canAccessDocuments()
				? searchKnowledgeFiles(
						localStorage.token,
						query.trim() || null,
						null,
						'updated_at',
						'asc',
						1
					).catch(() => null)
				: Promise.resolve(null)
		]);

		archivedChats = archivedRes ?? [];
		pinnedChatItems = pinnedRes ?? [];
		allNotes = Array.isArray(notesRes) ? notesRes : [];
		candidateDocuments = documentsRes?.items ?? [];

		loading = false;
		loaded = true;
	};

	$: filteredPinnedChats = (pinnedChatItems ?? []).filter((chat) =>
		matchesQuery(getChatTitle(chat), chat?.folder?.name, chat?.tag_names?.join(' '))
	);

	$: archivedNotes = (allNotes ?? [])
		.filter((note) => {
			const noteTimestamp = getNoteTimestamp(note);
			return (
				isOlderThan(noteTimestamp, archiveWindowDays) &&
				matchesQuery(getNoteTitle(note), note?.data?.content?.md)
			);
		})
		.sort((a, b) => (getNoteTimestamp(a) ?? 0) - (getNoteTimestamp(b) ?? 0));

	$: archivedDocuments = (candidateDocuments ?? [])
		.filter((document) => {
			const documentTimestamp = getDocumentTimestamp(document);
			return (
				isOlderThan(documentTimestamp, archiveWindowDays) &&
				matchesQuery(
					getDocumentTitle(document),
					document?.description,
					document?.collection_name || document?.meta?.collection_name
				)
			);
		})
		.sort((a, b) => (getDocumentTimestamp(a) ?? 0) - (getDocumentTimestamp(b) ?? 0));

	const openArchivedChat = async (chatId: string) => {
		await goto(`/c/${chatId}`);
	};

	const openPinnedChat = async (chatId: string) => {
		await goto(`/c/${chatId}`);
	};

	const openArchivedNote = async (noteId: string) => {
		await goto(`/notes/${noteId}`);
	};

	const openArchivedDocument = async (document) => {
		const title = getDocumentTitle(document);
		await goto(`/search?q=${encodeURIComponent(title)}`);
	};

	const unarchiveChatHandler = async (chatId: string) => {
		const res = await archiveChatById(localStorage.token, chatId).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success('Conversa restaurada.');
			await loadArchiveData();
		}
	};

	const unarchiveAllHandler = async () => {
		restoringAll = true;

		try {
			await unarchiveAllChats(localStorage.token);
			toast.success('Todas as conversas arquivadas foram restauradas.');
			await loadArchiveData();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			restoringAll = false;
		}
	};

	const exportArchivedChats = async () => {
		exporting = true;

		try {
			const chats = await getAllArchivedChats(localStorage.token);
			const blob = new Blob([JSON.stringify(chats)], {
				type: 'application/json'
			});
			saveAs(blob, `arquivo-dochat-${Date.now()}.json`);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			exporting = false;
		}
	};

	onMount(async () => {
		await loadArchiveData();
	});

	$: if (loaded && query !== undefined) {
		clearTimeout(refreshTimer);
		refreshTimer = setTimeout(async () => {
			await loadArchiveData();
		}, 250);
	}

	onDestroy(() => {
		clearTimeout(refreshTimer);
	});
</script>

<svelte:head>
	<title>Arquivo • {$WEBUI_NAME}</title>
</svelte:head>

<div class="dochat-archive-page">
	<section class="dochat-archive-hero">
		<div>
			<div class="dochat-archive-kicker">Arquivo</div>
			<h1>Historico e recuperacao</h1>
			<p>
				Reuna conversas arquivadas, itens salvos, notas antigas e documentos menos recentes em
				um unico lugar.
			</p>
		</div>

		<div class="dochat-archive-hero-actions">
			<button
				type="button"
				class="dochat-archive-secondary"
				on:click={exportArchivedChats}
				disabled={exporting}
				aria-label="Exportar conversas arquivadas"
			>
				{#if exporting}
					<Spinner className="size-4" />
				{:else}
					Exportar conversas
				{/if}
			</button>

			<button
				type="button"
				class="dochat-archive-primary"
				on:click={unarchiveAllHandler}
				disabled={restoringAll || archivedChats.length === 0}
				aria-label="Restaurar todas as conversas arquivadas"
			>
				{#if restoringAll}
					<Spinner className="size-4" />
				{:else}
					Restaurar tudo
				{/if}
			</button>
		</div>
	</section>

	<section class="dochat-archive-summary">
		<div class="dochat-archive-summary-card">
			<span>Conversas arquivadas</span>
			<strong>{archivedChats.length}</strong>
		</div>
		<div class="dochat-archive-summary-card">
			<span>Itens salvos</span>
			<strong>{filteredPinnedChats.length}</strong>
		</div>
		{#if canAccessNotes()}
			<div class="dochat-archive-summary-card">
				<span>Notas antigas</span>
				<strong>{archivedNotes.length}</strong>
			</div>
		{/if}
		{#if canAccessDocuments()}
			<div class="dochat-archive-summary-card">
				<span>Documentos antigos</span>
				<strong>{archivedDocuments.length}</strong>
			</div>
		{/if}
	</section>

	<section class="dochat-archive-toolbar">
		<div class="dochat-archive-search">
			<Search className="size-4" />
			<input
				bind:value={query}
				placeholder="Buscar no arquivo"
				aria-label="Buscar no arquivo"
			/>
		</div>

		<label class="dochat-archive-window">
			<span>Sem atividade ha pelo menos</span>
			<select bind:value={archiveWindowDays} aria-label="Periodo de inatividade">
				<option value={30}>30 dias</option>
				<option value={60}>60 dias</option>
				<option value={90}>90 dias</option>
			</select>
		</label>
	</section>

	{#if !loaded || loading}
		<div class="dochat-archive-loading">
			<Spinner className="size-5" />
			<span>Organizando o arquivo...</span>
		</div>
	{:else}
		<div class="dochat-archive-grid">
			<section class="dochat-archive-panel">
				<div class="dochat-archive-panel-head">
					<div class="dochat-archive-panel-copy">
						<div class="dochat-archive-panel-kicker">
							<ArchiveBox className="size-4" strokeWidth="1.8" />
							<span>Conversas arquivadas</span>
						</div>
						<p>Conversas removidas da linha principal, prontas para consulta ou restauracao.</p>
					</div>

					<a href="/search" class="dochat-archive-link">Abrir pesquisa tranversal</a>
				</div>

				{#if archivedChats.length === 0}
					<div class="dochat-archive-empty">
						<div>Nada arquivado por aqui.</div>
						<p>As conversas arquivadas vao aparecer nesta area quando sairem da navegacao principal.</p>
					</div>
				{:else}
					<div class="dochat-archive-list">
						{#each archivedChats.slice(0, 8) as chat}
							<div class="dochat-archive-item">
								<div class="dochat-archive-item-copy">
									<div class="dochat-archive-item-title">{getChatTitle(chat)}</div>
									<div class="dochat-archive-item-meta">
										{#if getChatTimestamp(chat)}
											<span>{dayjs(getChatTimestamp(chat)).fromNow()}</span>
										{/if}
									</div>
								</div>

								<div class="dochat-archive-item-actions">
									<button
										type="button"
										class="dochat-archive-action"
										on:click={() => openArchivedChat(chat.id)}
									>
										Abrir
									</button>
									<button
										type="button"
										class="dochat-archive-action"
										on:click={() => unarchiveChatHandler(chat.id)}
									>
										Restaurar
									</button>
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</section>

			<section class="dochat-archive-panel">
				<div class="dochat-archive-panel-head">
					<div class="dochat-archive-panel-copy">
						<div class="dochat-archive-panel-kicker">
							<ChatBubbles className="size-4" strokeWidth="1.8" />
							<span>Itens salvos</span>
						</div>
						<p>Conversas fixadas para consulta rapida e reabertura frequente.</p>
					</div>

					<a href="/" class="dochat-archive-link">Ir para chat</a>
				</div>

				{#if filteredPinnedChats.length === 0}
					<div class="dochat-archive-empty">
						<div>Nenhum item salvo.</div>
						<p>Use a opcao de fixar conversa para transformar historicos importantes em atalhos.</p>
					</div>
				{:else}
					<div class="dochat-archive-list">
						{#each filteredPinnedChats.slice(0, 8) as chat}
							<div class="dochat-archive-item">
								<div class="dochat-archive-item-copy">
									<div class="dochat-archive-item-title">{getChatTitle(chat)}</div>
									<div class="dochat-archive-item-meta">
										{#if getChatTimestamp(chat)}
											<span>Atualizada {dayjs(getChatTimestamp(chat)).fromNow()}</span>
										{/if}
									</div>
								</div>

								<div class="dochat-archive-item-actions">
									<button
										type="button"
										class="dochat-archive-action"
										on:click={() => openPinnedChat(chat.id)}
									>
										Abrir
									</button>
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</section>

			{#if canAccessNotes()}
				<section class="dochat-archive-panel">
					<div class="dochat-archive-panel-head">
						<div class="dochat-archive-panel-copy">
							<div class="dochat-archive-panel-kicker dochat-archive-panel-kicker-notes">
								<Note className="size-4" strokeWidth="1.8" />
								<span>Notas antigas</span>
							</div>
							<p>Notas sem atividade recente, ideais para revisao, limpeza ou reaproveitamento.</p>
						</div>

						<a href="/notes" class="dochat-archive-link">Abrir notas</a>
					</div>

					{#if archivedNotes.length === 0}
						<div class="dochat-archive-empty">
							<div>Nenhuma nota antiga neste recorte.</div>
							<p>Ajuste o periodo para ampliar a memoria de trabalho preservada no arquivo.</p>
						</div>
					{:else}
						<div class="dochat-archive-list">
							{#each archivedNotes.slice(0, 8) as note}
								<div class="dochat-archive-item">
									<div class="dochat-archive-item-copy">
										<div class="dochat-archive-item-title">{getNoteTitle(note)}</div>
										<p class="dochat-archive-item-preview">{getNotePreview(note)}</p>
										<div class="dochat-archive-item-meta">
											{#if getNoteTimestamp(note)}
												<span>Atualizada {dayjs(getNoteTimestamp(note)).fromNow()}</span>
											{/if}
										</div>
									</div>

									<div class="dochat-archive-item-actions">
										<button
											type="button"
											class="dochat-archive-action"
											on:click={() => openArchivedNote(note.id)}
										>
											Abrir
										</button>
									</div>
								</div>
							{/each}
						</div>
					{/if}
				</section>
			{/if}

			{#if canAccessDocuments()}
				<section class="dochat-archive-panel">
					<div class="dochat-archive-panel-head">
						<div class="dochat-archive-panel-copy">
							<div class="dochat-archive-panel-kicker dochat-archive-panel-kicker-documents">
								<Document className="size-4" strokeWidth="1.8" />
								<span>Documentos antigos</span>
							</div>
							<p>Documentos menos recentes para reler, reindexar ou reencontrar pelo contexto.</p>
						</div>

						<a href="/workspace/knowledge" class="dochat-archive-link">Abrir documentos</a>
					</div>

					{#if archivedDocuments.length === 0}
						<div class="dochat-archive-empty">
							<div>Nenhum documento antigo encontrado.</div>
							<p>Se houver documentos fora do recorte atual, eles aparecerao quando o periodo for ampliado.</p>
						</div>
					{:else}
						<div class="dochat-archive-list">
							{#each archivedDocuments.slice(0, 8) as document}
								<div class="dochat-archive-item">
									<div class="dochat-archive-item-copy">
										<div class="dochat-archive-item-title">{getDocumentTitle(document)}</div>
										<p class="dochat-archive-item-preview">{getDocumentPreview(document)}</p>
										<div class="dochat-archive-item-meta">
											{#if document?.collection_name || document?.meta?.collection_name}
												<span>{document?.collection_name || document?.meta?.collection_name}</span>
											{/if}
											{#if document?.meta?.size}
												<span>{formatFileSize(document.meta.size)}</span>
											{/if}
											{#if getDocumentTimestamp(document)}
												<span>{dayjs(getDocumentTimestamp(document)).fromNow()}</span>
											{/if}
										</div>
									</div>

									<div class="dochat-archive-item-actions">
										<button
											type="button"
											class="dochat-archive-action"
											on:click={() => openArchivedDocument(document)}
										>
											Buscar
										</button>
									</div>
								</div>
							{/each}
						</div>
					{/if}
				</section>
			{/if}
		</div>
	{/if}
</div>

<style>
	.dochat-archive-page {
		min-height: 100dvh;
		padding: 1rem;
		background:
			radial-gradient(circle at top left, rgba(232, 239, 228, 0.58), transparent 30%),
			linear-gradient(180deg, rgba(251, 249, 245, 0.98), rgba(247, 244, 238, 0.96));
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-archive-hero,
	.dochat-archive-summary-card,
	.dochat-archive-toolbar,
	.dochat-archive-panel {
		border: 1px solid rgba(221, 214, 202, 0.86);
		background: rgba(255, 255, 255, 0.84);
		box-shadow: 0 18px 36px rgba(84, 74, 58, 0.06);
		backdrop-filter: blur(14px);
	}

	.dochat-archive-hero {
		display: flex;
		align-items: end;
		justify-content: space-between;
		gap: 1rem;
		max-width: 96rem;
		margin: 0 auto;
		padding: 1.5rem;
		border-radius: 1.75rem;
	}

	.dochat-archive-kicker {
		font-size: 0.76rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--dochat-accent, #6f8a64);
	}

	.dochat-archive-hero h1 {
		font-size: clamp(1.75rem, 3vw, 2.45rem);
		font-weight: 700;
		letter-spacing: -0.03em;
	}

	.dochat-archive-hero p {
		margin-top: 0.35rem;
		max-width: 40rem;
		line-height: 1.6;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-archive-hero-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
	}

	.dochat-archive-primary,
	.dochat-archive-secondary,
	.dochat-archive-link,
	.dochat-archive-action {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 0.5rem;
		padding: 0.78rem 1rem;
		border-radius: 9999px;
		font-size: 0.84rem;
		font-weight: 700;
		transition:
			background-color 160ms ease,
			color 160ms ease,
			transform 160ms ease;
	}

	.dochat-archive-primary {
		background: var(--dochat-accent, #6f8a64);
		color: white;
	}

	.dochat-archive-primary:disabled,
	.dochat-archive-secondary:disabled {
		opacity: 0.55;
		cursor: not-allowed;
	}

	.dochat-archive-secondary,
	.dochat-archive-link,
	.dochat-archive-action {
		background: rgba(247, 244, 238, 0.96);
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-archive-primary:hover,
	.dochat-archive-secondary:hover,
	.dochat-archive-link:hover,
	.dochat-archive-action:hover {
		transform: translateY(-1px);
	}

	.dochat-archive-summary,
	.dochat-archive-grid {
		max-width: 96rem;
		margin: 1rem auto 0;
	}

	.dochat-archive-summary {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 1rem;
	}

	.dochat-archive-summary-card {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		padding: 1rem 1.15rem;
		border-radius: 1.35rem;
	}

	.dochat-archive-summary-card span {
		font-size: 0.8rem;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-archive-summary-card strong {
		font-size: 1.5rem;
		font-weight: 700;
		letter-spacing: -0.03em;
	}

	.dochat-archive-toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		max-width: 96rem;
		margin: 1rem auto 0;
		padding: 1rem;
		border-radius: 1.6rem;
	}

	.dochat-archive-search {
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

	.dochat-archive-search input,
	.dochat-archive-window select {
		background: transparent;
		outline: none;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-archive-search input {
		flex: 1;
		min-width: 0;
	}

	.dochat-archive-window {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding: 0.9rem 1rem;
		border: 1px solid rgba(231, 225, 216, 0.96);
		border-radius: 1.25rem;
		background: rgba(251, 249, 245, 0.92);
		font-size: 0.84rem;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-archive-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 1rem;
	}

	.dochat-archive-panel {
		display: flex;
		flex-direction: column;
		padding: 1rem;
		border-radius: 1.65rem;
		min-height: 24rem;
	}

	.dochat-archive-panel-head {
		display: flex;
		align-items: start;
		justify-content: space-between;
		gap: 1rem;
		padding-bottom: 0.9rem;
		border-bottom: 1px solid rgba(231, 225, 216, 0.92);
	}

	.dochat-archive-panel-kicker {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		font-size: 0.8rem;
		font-weight: 700;
		color: var(--dochat-accent, #6f8a64);
	}

	.dochat-archive-panel-kicker-notes {
		color: #8a6d2e;
	}

	.dochat-archive-panel-kicker-documents {
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-archive-panel-copy p {
		margin-top: 0.35rem;
		line-height: 1.6;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-archive-list {
		display: flex;
		flex-direction: column;
		gap: 0.8rem;
		margin-top: 1rem;
	}

	.dochat-archive-item {
		display: flex;
		align-items: start;
		justify-content: space-between;
		gap: 1rem;
		padding: 1rem;
		border: 1px solid rgba(231, 225, 216, 0.9);
		border-radius: 1.25rem;
		background: rgba(251, 249, 245, 0.78);
	}

	.dochat-archive-item-copy {
		min-width: 0;
	}

	.dochat-archive-item-title {
		font-size: 0.98rem;
		font-weight: 700;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-archive-item-preview {
		margin-top: 0.35rem;
		line-height: 1.6;
		color: var(--dochat-text-soft, #5c5c62);
		display: -webkit-box;
		-webkit-line-clamp: 3;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	.dochat-archive-item-meta {
		display: flex;
		flex-wrap: wrap;
		gap: 0.55rem;
		margin-top: 0.45rem;
		font-size: 0.78rem;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-archive-item-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
	}

	.dochat-archive-loading,
	.dochat-archive-empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.6rem;
		padding: 2rem 1rem;
		text-align: center;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-archive-loading {
		min-height: 18rem;
	}

	.dochat-archive-empty p {
		max-width: 20rem;
		color: var(--dochat-text-faint, #8e8e93);
	}

	@media (max-width: 1100px) {
		.dochat-archive-summary,
		.dochat-archive-grid {
			grid-template-columns: 1fr 1fr;
		}
	}

	@media (max-width: 767px) {
		.dochat-archive-hero,
		.dochat-archive-toolbar,
		.dochat-archive-panel-head,
		.dochat-archive-item {
			flex-direction: column;
			align-items: stretch;
		}

		.dochat-archive-summary,
		.dochat-archive-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
