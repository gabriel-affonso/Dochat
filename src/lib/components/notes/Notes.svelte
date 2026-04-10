<script lang="ts">
	import { marked } from 'marked';
	import { toast } from 'svelte-sonner';
	import fileSaver from 'file-saver';

	const { saveAs } = fileSaver;

	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	async function loadLocale(locales) {
		for (const locale of locales) {
			try {
				dayjs.locale(locale);
				break;
			} catch (error) {
				console.error(`Could not load locale '${locale}':`, error);
			}
		}
	}

	import { onMount, getContext } from 'svelte';

	const i18n = getContext('i18n');
	$: loadLocale($i18n.languages);

	import { goto } from '$app/navigation';
	import { WEBUI_NAME } from '$lib/stores';
	import { createNewNote, deleteNoteById, getNoteById, searchNotes } from '$lib/apis/notes';
	import { capitalizeFirstLetter, copyToClipboard, getTimeRange } from '$lib/utils';
	import { downloadPdf, createNoteHandler } from './utils';

	import EllipsisHorizontal from '../icons/EllipsisHorizontal.svelte';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Search from '../icons/Search.svelte';
	import Plus from '../icons/Plus.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import NoteMenu from './Notes/NoteMenu.svelte';
	import FilesOverlay from '../chat/MessageInput/FilesOverlay.svelte';
	import XMark from '../icons/XMark.svelte';
	import DropdownOptions from '../common/DropdownOptions.svelte';
	import Loader from '../common/Loader.svelte';

	let loaded = false;

	let selectedNote = null;
	let showDeleteConfirm = false;

	let items = null;
	let total = null;

	let query = '';
	let searchDebounceTimer: ReturnType<typeof setTimeout>;

	let sortKey = null;
	let displayOption = null;
	let viewOption = null;
	let permission = null;

	let page = 1;

	let itemsLoading = false;
	let allItemsLoaded = false;

	const downloadHandler = async (type) => {
		const note = await getNoteById(localStorage.token, selectedNote.id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (!note) return;

		if (type === 'txt') {
			const blob = new Blob([note.data.content.md], { type: 'text/plain' });
			saveAs(blob, `${note.title}.txt`);
		} else if (type === 'md') {
			const blob = new Blob([note.data.content.md], { type: 'text/markdown' });
			saveAs(blob, `${note.title}.md`);
		} else if (type === 'pdf') {
			try {
				await downloadPdf(note);
			} catch (error) {
				toast.error(`${error}`);
			}
		}
	};

	const deleteNoteHandler = async (id) => {
		const res = await deleteNoteById(localStorage.token, id).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			init();
		}
	};

	const inputFilesHandler = async (inputFiles) => {
		for (const file of inputFiles) {
			if (file.type !== 'text/markdown') {
				toast.error($i18n.t('Only markdown files are allowed'));
				return;
			}

			const reader = new FileReader();
			reader.onload = async (event) => {
				const content = event.target.result;
				let name = file.name.replace(/\.md$/, '');

				if (typeof content !== 'string') {
					toast.error($i18n.t('Invalid file content'));
					return;
				}

				const res = await createNewNote(localStorage.token, {
					title: name,
					data: {
						content: {
							json: null,
							html: marked.parse(content ?? ''),
							md: content
						}
					},
					meta: null,
					access_grants: []
				}).catch((error) => {
					toast.error(`${error}`);
					return null;
				});

				if (res) {
					init();
				}
			};

			reader.readAsText(file);
		}
	};

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
		await getItemsPage();
	};

	const init = async () => {
		reset();
		await getItemsPage();
	};

	$: if (query !== undefined) {
		clearTimeout(searchDebounceTimer);
		searchDebounceTimer = setTimeout(() => {
			if (loaded) {
				init();
			}
		}, 300);
	}

	$: if (loaded && sortKey !== undefined && permission !== undefined && viewOption !== undefined) {
		init();
	}

	const getItemsPage = async () => {
		itemsLoading = true;

		if (viewOption === 'created') {
			permission = null;
		}

		const res = await searchNotes(
			localStorage.token,
			query,
			viewOption,
			permission,
			sortKey,
			page
		).catch(() => {
			return null;
		});

		if (res) {
			total = res.total;
			const pageItems = res.items;

			allItemsLoaded = (pageItems ?? []).length === 0;
			items = items ? [...items, ...pageItems] : pageItems;
		}

		itemsLoading = false;
		return res;
	};

	const groupNotes = (res) => {
		if (!Array.isArray(res)) {
			return [];
		}

		const grouped: Record<string, any[]> = {};
		const orderedKeys: string[] = [];

		for (const note of res) {
			const timeRange = getTimeRange(note.updated_at / 1000000000);
			if (!grouped[timeRange]) {
				grouped[timeRange] = [];
				orderedKeys.push(timeRange);
			}
			grouped[timeRange].push({
				...note,
				timeRange
			});
		}

		return orderedKeys.map((key) => [key, grouped[key]] as [string, any[]]);
	};

	let dragged = false;

	const onDragOver = (e) => {
		e.preventDefault();
		dragged = e.dataTransfer?.types?.includes('Files') ?? false;
	};

	const onDragLeave = () => {
		dragged = false;
	};

	const onDrop = async (e) => {
		e.preventDefault();

		if (e.dataTransfer?.files) {
			const inputFiles = Array.from(e.dataTransfer?.files);
			if (inputFiles && inputFiles.length > 0) {
				inputFilesHandler(inputFiles);
			}
		}

		dragged = false;
	};

	onMount(() => {
		viewOption = localStorage?.noteViewOption ?? null;
		displayOption = localStorage?.noteDisplayOption ?? null;

		loaded = true;

		const dropzoneElement = document.getElementById('notes-container');
		dropzoneElement?.addEventListener('dragover', onDragOver);
		dropzoneElement?.addEventListener('drop', onDrop);
		dropzoneElement?.addEventListener('dragleave', onDragLeave);

		return () => {
			clearTimeout(searchDebounceTimer);

			if (dropzoneElement) {
				dropzoneElement?.removeEventListener('dragover', onDragOver);
				dropzoneElement?.removeEventListener('drop', onDrop);
				dropzoneElement?.removeEventListener('dragleave', onDragLeave);
			}
		};
	});
</script>

<svelte:head>
	<title>Notas • {$WEBUI_NAME}</title>
</svelte:head>

<FilesOverlay show={dragged} />

<div id="notes-container" class="dochat-notes-page">
	{#if loaded}
		<DeleteConfirmDialog
			bind:show={showDeleteConfirm}
			title="Remover nota?"
			on:confirm={() => {
				deleteNoteHandler(selectedNote?.id);
				showDeleteConfirm = false;
			}}
		>
			<div class="text-sm text-[var(--dochat-text-soft)] truncate">
				Esta acao remove <span class="font-semibold">{selectedNote?.title}</span>.
			</div>
		</DeleteConfirmDialog>

		<section class="dochat-notes-hero">
			<div>
				<div class="dochat-notes-hero-kicker">Notas</div>
				<h1>Notas recentes</h1>
				<p>
					Um espaco de escrita leve para capturar memoria de trabalho, rascunhos e ideias em
					andamento.
				</p>
			</div>

			<button
				type="button"
				class="dochat-notes-primary"
				on:click={async () => {
					const res = await createNoteHandler(dayjs().format('YYYY-MM-DD'));

					if (res) {
						goto(`/notes/${res.id}`);
					}
				}}
				aria-label="Nova nota"
			>
				<Plus className="size-3.5" strokeWidth="2.5" />
				<span>Nova nota</span>
			</button>
		</section>

		<section class="dochat-notes-summary">
			<div class="dochat-notes-summary-card">
				<span>Notas</span>
				<strong>{total ?? 0}</strong>
			</div>
			<div class="dochat-notes-summary-card">
				<span>Modo</span>
				<strong>{displayOption === 'grid' ? 'Grade' : 'Lista'}</strong>
			</div>
			<div class="dochat-notes-summary-card">
				<span>Busca</span>
				<strong>{query ? 'Filtrada' : 'Completa'}</strong>
			</div>
		</section>

		<section class="dochat-notes-panel">
			<div class="dochat-notes-toolbar">
				<div class="dochat-notes-search">
					<Search className="size-4" />
					<input
						bind:value={query}
						placeholder="Buscar notas"
						aria-label="Buscar notas"
					/>

					{#if query}
						<button
							class="dochat-notes-clear"
							type="button"
							aria-label="Limpar busca"
							on:click={() => {
								query = '';
							}}
						>
							<XMark className="size-3.5" strokeWidth="2" />
						</button>
					{/if}
				</div>

				<div class="dochat-notes-filters">
					<DropdownOptions
						align="start"
						className="flex items-center gap-2 truncate rounded-full border border-[rgba(231,225,216,0.96)] bg-[rgba(251,249,245,0.92)] px-3 py-2 text-sm text-[var(--dochat-text-soft)]"
						bind:value={viewOption}
						items={[
							{ value: null, label: 'Todas' },
							{ value: 'created', label: 'Criadas por voce' },
							{ value: 'shared', label: 'Compartilhadas' }
						]}
						onChange={(value) => {
							if (value) {
								localStorage.noteViewOption = value;
							} else {
								delete localStorage.noteViewOption;
							}
						}}
					/>

					{#if [null, 'shared'].includes(viewOption)}
						<DropdownOptions
							align="start"
							className="flex items-center gap-2 truncate rounded-full border border-[rgba(231,225,216,0.96)] bg-[rgba(251,249,245,0.92)] px-3 py-2 text-sm text-[var(--dochat-text-soft)]"
							bind:value={permission}
							items={[
								{ value: null, label: 'Com edicao' },
								{ value: 'read_only', label: 'Somente leitura' }
							]}
						/>
					{/if}

					<DropdownOptions
						align="start"
						className="flex items-center gap-2 truncate rounded-full border border-[rgba(231,225,216,0.96)] bg-[rgba(251,249,245,0.92)] px-3 py-2 text-sm text-[var(--dochat-text-soft)]"
						bind:value={displayOption}
						items={[
							{ value: null, label: 'Lista' },
							{ value: 'grid', label: 'Grade' }
						]}
						onChange={() => {
							if (displayOption) {
								localStorage.noteDisplayOption = displayOption;
							} else {
								delete localStorage.noteDisplayOption;
							}
						}}
					/>
				</div>
			</div>

			{#if items !== null && total !== null}
				{#if (items ?? []).length > 0}
					{@const groupedNotes = groupNotes(items)}

					<div class="dochat-notes-groups">
						{#each groupedNotes as [timeRange, notesList], idx}
							<div class="dochat-notes-group-label">{$i18n.t(timeRange)}</div>

							{#if displayOption === null}
								<div class="dochat-notes-list">
									{#each notesList as note (note.id)}
										<div class="dochat-note-row">
											<a href={`/notes/${note.id}`} class="dochat-note-link">
												<div class="dochat-note-main">
													<div class="dochat-note-title">{note.title}</div>
													<div class="dochat-note-preview">
														{#if note.data?.content?.md}
															{note.data.content.md}
														{:else}
															Sem conteudo
														{/if}
													</div>
												</div>

												<div class="dochat-note-meta">
													<Tooltip content={dayjs(note.updated_at / 1000000).format('LLLL')}>
														<div>{dayjs(note.updated_at / 1000000).fromNow()}</div>
													</Tooltip>

													<Tooltip
														content={note?.user?.email ?? $i18n.t('Deleted User')}
														className="flex shrink-0"
														placement="top-start"
													>
														<div>
															Por {capitalizeFirstLetter(
																note?.user?.name ?? note?.user?.email ?? 'Deleted User'
															)}
														</div>
													</Tooltip>
												</div>
											</a>

											<NoteMenu
												onDownload={(type) => {
													selectedNote = note;
													downloadHandler(type);
												}}
												onCopyLink={async () => {
													const baseUrl = window.location.origin;
													const res = await copyToClipboard(`${baseUrl}/notes/${note.id}`);

													if (res) {
														toast.success('Link copiado.');
													} else {
														toast.error('Nao foi possivel copiar o link.');
													}
												}}
												onDelete={() => {
													selectedNote = note;
													showDeleteConfirm = true;
												}}
											>
												<button
													class="dochat-note-menu"
													type="button"
													aria-label="Mais opcoes da nota"
												>
													<EllipsisHorizontal className="size-5" />
												</button>
											</NoteMenu>
										</div>
									{/each}
								</div>
							{:else}
								<div class="dochat-notes-grid">
									{#each notesList as note (note.id)}
										<div class="dochat-note-card">
											<div class="dochat-note-card-head">
												<a href={`/notes/${note.id}`} class="dochat-note-card-title-link">
													<div class="dochat-note-title">{note.title}</div>
												</a>

												<NoteMenu
													onDownload={(type) => {
														selectedNote = note;
														downloadHandler(type);
													}}
													onCopyLink={async () => {
														const baseUrl = window.location.origin;
														const res = await copyToClipboard(`${baseUrl}/notes/${note.id}`);

														if (res) {
															toast.success('Link copiado.');
														} else {
															toast.error('Nao foi possivel copiar o link.');
														}
													}}
													onDelete={() => {
														selectedNote = note;
														showDeleteConfirm = true;
													}}
												>
													<button
														class="dochat-note-menu"
														type="button"
														aria-label="Mais opcoes da nota"
													>
														<EllipsisHorizontal className="size-5" />
													</button>
												</NoteMenu>
											</div>

											<a href={`/notes/${note.id}`} class="dochat-note-card-link">
												<div class="dochat-note-preview dochat-note-preview-card">
													{#if note.data?.content?.md}
														{note.data.content.md}
													{:else}
														Sem conteudo
													{/if}
												</div>

												<div class="dochat-note-card-footer">
													<div>{dayjs(note.updated_at / 1000000).fromNow()}</div>
													<div>
														Por {capitalizeFirstLetter(
															note?.user?.name ?? note?.user?.email ?? 'Deleted User'
														)}
													</div>
												</div>
											</a>
										</div>
									{/each}
								</div>
							{/if}

							{#if idx !== groupedNotes.length - 1}
								<div class="dochat-notes-divider"></div>
							{/if}
						{/each}

						{#if !allItemsLoaded}
							<Loader
								on:visible={() => {
									if (!itemsLoading) {
										loadMoreItems();
									}
								}}
							>
								<div class="dochat-notes-loading">
									<Spinner className="size-4" />
									<span>Carregando mais notas...</span>
								</div>
							</Loader>
						{/if}
					</div>
				{:else}
					<div class="dochat-notes-empty">
						<div>Nenhuma nota encontrada.</div>
						<p>Crie uma nova nota para começar a registrar ideias e contexto.</p>
					</div>
				{/if}
			{:else}
				<div class="dochat-notes-loading">
					<Spinner className="size-4" />
					<span>Carregando notas...</span>
				</div>
			{/if}
		</section>
	{:else}
		<div class="dochat-notes-loading">
			<Spinner className="size-4" />
		</div>
	{/if}
</div>

<style>
	.dochat-notes-page {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		min-height: 100%;
		padding: 1rem 0 1.25rem;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-notes-hero,
	.dochat-notes-panel,
	.dochat-notes-summary-card {
		border: 1px solid rgba(221, 214, 202, 0.86);
		background: rgba(255, 255, 255, 0.84);
		box-shadow: 0 18px 36px rgba(84, 74, 58, 0.06);
		backdrop-filter: blur(14px);
	}

	.dochat-notes-hero {
		display: flex;
		align-items: end;
		justify-content: space-between;
		gap: 1rem;
		padding: 1.45rem;
		border-radius: 1.7rem;
	}

	.dochat-notes-hero-kicker {
		font-size: 0.76rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: #8a6d2e;
	}

	.dochat-notes-hero h1 {
		font-size: clamp(1.75rem, 3vw, 2.3rem);
		font-weight: 700;
		letter-spacing: -0.03em;
	}

	.dochat-notes-hero p {
		margin-top: 0.35rem;
		max-width: 42rem;
		line-height: 1.6;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-notes-primary {
		display: inline-flex;
		align-items: center;
		gap: 0.55rem;
		padding: 0.9rem 1.15rem;
		border-radius: 9999px;
		background: #b88b2d;
		color: white;
		font-size: 0.9rem;
		font-weight: 700;
	}

	.dochat-notes-summary {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 1rem;
	}

	.dochat-notes-summary-card {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		padding: 1rem 1.15rem;
		border-radius: 1.35rem;
	}

	.dochat-notes-summary-card span {
		font-size: 0.8rem;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-notes-summary-card strong {
		font-size: 1.45rem;
		font-weight: 700;
		letter-spacing: -0.03em;
	}

	.dochat-notes-panel {
		border-radius: 1.7rem;
		padding: 1rem;
	}

	.dochat-notes-toolbar {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
	}

	.dochat-notes-search {
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

	.dochat-notes-search input {
		flex: 1;
		min-width: 0;
		background: transparent;
		outline: none;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-notes-search input::placeholder {
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-notes-clear {
		padding: 0.3rem;
		border-radius: 9999px;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-notes-clear:hover {
		background: rgba(247, 244, 238, 0.96);
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-notes-filters {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
	}

	.dochat-notes-groups {
		margin-top: 1rem;
	}

	.dochat-notes-group-label {
		padding: 0 0.25rem 0.8rem;
		font-size: 0.78rem;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-notes-list {
		display: flex;
		flex-direction: column;
		gap: 0.8rem;
	}

	.dochat-note-row,
	.dochat-note-card {
		border: 1px solid rgba(231, 225, 216, 0.9);
		border-radius: 1.35rem;
		background: rgba(251, 249, 245, 0.82);
	}

	.dochat-note-row {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.95rem 1rem;
	}

	.dochat-note-link,
	.dochat-note-card-link,
	.dochat-note-card-title-link {
		display: flex;
		color: inherit;
		text-decoration: none;
	}

	.dochat-note-link {
		flex: 1;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
	}

	.dochat-note-card-title-link {
		flex: 1;
		min-width: 0;
	}

	.dochat-note-main {
		min-width: 0;
	}

	.dochat-note-title {
		font-size: 1rem;
		font-weight: 700;
		color: var(--dochat-text, #1d1d1f);
		line-height: 1.35;
	}

	.dochat-note-preview {
		margin-top: 0.35rem;
		color: var(--dochat-text-soft, #5c5c62);
		line-height: 1.65;
		display: -webkit-box;
		-webkit-line-clamp: 3;
		-webkit-box-orient: vertical;
		overflow: hidden;
	}

	.dochat-note-meta,
	.dochat-note-card-footer {
		display: flex;
		flex-direction: column;
		align-items: flex-end;
		gap: 0.35rem;
		font-size: 0.78rem;
		color: var(--dochat-text-faint, #8e8e93);
		white-space: nowrap;
	}

	.dochat-note-menu {
		padding: 0.45rem;
		border-radius: 9999px;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-note-menu:hover {
		background: rgba(247, 244, 238, 0.96);
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-notes-grid {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 1rem;
	}

	.dochat-note-card {
		padding: 1rem;
	}

	.dochat-note-card-link {
		flex: 1;
		flex-direction: column;
		gap: 0.9rem;
	}

	.dochat-note-card-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.75rem;
	}

	.dochat-note-preview-card {
		min-height: 4.8rem;
	}

	.dochat-note-card-footer {
		align-items: flex-start;
	}

	.dochat-notes-divider {
		height: 1px;
		margin: 1rem 0 1.25rem;
		background: rgba(231, 225, 216, 0.88);
	}

	.dochat-notes-loading,
	.dochat-notes-empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.6rem;
		padding: 2rem 1rem;
		text-align: center;
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-notes-empty p {
		max-width: 22rem;
		color: var(--dochat-text-faint, #8e8e93);
	}

	@media (max-width: 1100px) {
		.dochat-notes-grid {
			grid-template-columns: repeat(2, minmax(0, 1fr));
		}
	}

	@media (max-width: 767px) {
		.dochat-notes-hero,
		.dochat-notes-toolbar,
		.dochat-note-link {
			flex-direction: column;
			align-items: stretch;
		}

		.dochat-notes-summary,
		.dochat-notes-grid {
			grid-template-columns: 1fr;
		}

		.dochat-note-meta {
			align-items: flex-start;
			white-space: normal;
		}
	}
</style>
