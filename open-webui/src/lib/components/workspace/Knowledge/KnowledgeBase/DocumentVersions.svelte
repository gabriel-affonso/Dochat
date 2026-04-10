<script lang="ts">
	import dayjs from '$lib/dayjs';

	import { compareDocumentVersionsById, getDocumentVersionsById } from '$lib/apis/documents';
	import Spinner from '$lib/components/common/Spinner.svelte';

	type VersionActor = {
		id?: string | null;
		name?: string | null;
		email?: string | null;
	};

	type VersionFile = {
		id?: string | null;
	};

	type DocumentVersion = {
		revision: number;
		is_current?: boolean;
		created_at?: number | null;
		created_by?: VersionActor | null;
		line_count?: number;
	};

	type DocumentVersionComparison = {
		base_version: DocumentVersion;
		target_version: DocumentVersion;
		summary?: {
			added_lines?: number;
			removed_lines?: number;
		};
		lines: Array<{
			type: 'added' | 'removed' | 'unchanged';
			old_line?: number | null;
			new_line?: number | null;
			text?: string | null;
		}>;
	};

	export let token = '';
	export let file: VersionFile | null = null;
	export let active = false;
	export let initialTargetRevision: number | null = null;

	let loading = false;
	let compareLoading = false;
	let error = '';
	let versions: DocumentVersion[] = [];
	let loadedFileId: string | null = null;
	let baseRevision: number | null = null;
	let targetRevision: number | null = null;
	let comparison: DocumentVersionComparison | null = null;
	let lastComparedKey = '';

	const loadVersions = async (fileId: string) => {
		if (!token || !fileId) return;

		loading = true;
		error = '';
		comparison = null;
		lastComparedKey = '';
		baseRevision = null;
		targetRevision = null;

		try {
			versions = (await getDocumentVersionsById(token, fileId)) ?? [];
			const requestedVersion = initialTargetRevision
				? versions.find((version) => version.revision === initialTargetRevision)
				: null;
			const fallbackBaseVersion =
				versions.find((version) => version.revision !== requestedVersion?.revision) ?? null;

			if (requestedVersion) {
				targetRevision = requestedVersion.revision;
				baseRevision = fallbackBaseVersion?.revision ?? requestedVersion.revision;
			} else if (versions.length > 1) {
				targetRevision = versions[0].revision;
				baseRevision = versions[1].revision;
			} else if (versions.length === 1) {
				targetRevision = versions[0].revision;
				baseRevision = versions[0].revision;
			}
		} catch (err) {
			console.error(err);
			error = String(err || 'Nao foi possivel carregar o historico de versoes.');
			versions = [];
		} finally {
			loading = false;
		}
	};

	const loadComparison = async () => {
		if (!file?.id || baseRevision === null || targetRevision === null) return;

		compareLoading = true;
		error = '';

		try {
			lastComparedKey = `${file.id}:${baseRevision}:${targetRevision}`;
			comparison = await compareDocumentVersionsById(token, file.id, {
				base_revision: Number(baseRevision),
				target_revision: Number(targetRevision)
			});
		} catch (err) {
			console.error(err);
			error = String(err || 'Nao foi possivel comparar as versoes.');
			comparison = null;
		} finally {
			compareLoading = false;
		}
	};

	$: if (active && file?.id && loadedFileId !== file.id) {
		loadedFileId = file.id;
		void loadVersions(file.id);
	}

	$: if (
		active &&
		file?.id &&
		baseRevision !== null &&
		targetRevision !== null &&
		!loading &&
		!compareLoading &&
		lastComparedKey !== `${file.id}:${baseRevision}:${targetRevision}`
	) {
		void loadComparison();
	}
</script>

<div class="flex h-full min-h-0 flex-col gap-3">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<div>
			<div class="text-xs uppercase tracking-[0.08em] text-[var(--dochat-accent)]">
				Historico de versoes
			</div>
			<div class="mt-1 text-sm text-[var(--dochat-text-soft)]">
				Cada check-in gera uma nova revisao auditavel do Markdown.
			</div>
		</div>
		{#if versions.length > 0}
			<div class="text-xs text-[var(--dochat-text-soft)]">
				{versions.length} versao(oes) registadas
			</div>
		{/if}
	</div>

	{#if loading}
		<div
			class="flex h-full items-center justify-center rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.72)]"
		>
			<Spinner className="size-4" />
		</div>
	{:else if error}
		<div
			class="rounded-3xl border border-[rgba(179,90,90,0.24)] bg-[rgba(255,245,245,0.8)] px-4 py-3 text-sm text-[#9a4d4d]"
		>
			{error}
		</div>
	{:else if versions.length === 0}
		<div
			class="flex h-full min-h-[20rem] items-center justify-center rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.72)] text-sm text-[var(--dochat-text-soft)]"
		>
			Ainda nao ha revisoes guardadas para este documento.
		</div>
	{:else}
		<div class="grid min-h-0 flex-1 gap-3 lg:grid-cols-[20rem_minmax(0,1fr)]">
			<div
				class="min-h-0 overflow-auto rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.72)] p-3"
			>
				<div class="grid gap-2">
					<label class="flex flex-col gap-1 text-xs text-[var(--dochat-text-soft)]">
						<span>Versao base</span>
						<select
							class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-white px-3 py-2 text-sm text-[var(--dochat-text)] outline-none"
							bind:value={baseRevision}
						>
							{#each versions as version}
								<option value={version.revision}>Rev. {version.revision}</option>
							{/each}
						</select>
					</label>

					<label class="flex flex-col gap-1 text-xs text-[var(--dochat-text-soft)]">
						<span>Comparar com</span>
						<select
							class="rounded-2xl border border-[rgba(221,214,202,0.96)] bg-white px-3 py-2 text-sm text-[var(--dochat-text)] outline-none"
							bind:value={targetRevision}
						>
							{#each versions as version}
								<option value={version.revision}>Rev. {version.revision}</option>
							{/each}
						</select>
					</label>
				</div>

				<div class="mt-4 space-y-2">
					{#each versions as version}
						<button
							type="button"
							class="w-full rounded-2xl border border-[rgba(231,225,216,0.92)] bg-white px-3 py-3 text-left transition hover:border-[rgba(111,138,100,0.24)] hover:bg-[rgba(232,239,228,0.24)]"
							on:click={() => {
								if (version.is_current) {
									targetRevision = version.revision;
								} else {
									baseRevision = version.revision;
								}
							}}
						>
							<div class="flex items-center justify-between gap-2">
								<div class="text-sm font-semibold text-[var(--dochat-text)]">
									Rev. {version.revision}
								</div>
								{#if version.is_current}
									<span
										class="rounded-full bg-[var(--dochat-accent-soft)] px-2 py-0.5 text-[0.65rem] font-semibold uppercase tracking-[0.08em] text-[var(--dochat-accent)]"
									>
										Atual
									</span>
								{/if}
							</div>
							<div class="mt-1 text-xs text-[var(--dochat-text-soft)]">
								{version.created_by?.name ||
									version.created_by?.email ||
									version.created_by?.id ||
									'—'}
							</div>
							<div class="mt-1 text-[0.7rem] text-[var(--dochat-text-faint)]">
								{#if version.created_at}
									{dayjs(version.created_at * 1000).format('DD/MM/YYYY HH:mm')}
								{:else}
									Data indisponivel
								{/if}
								· {version.line_count} linhas
							</div>
						</button>
					{/each}
				</div>
			</div>

			<div
				class="min-h-0 overflow-hidden rounded-3xl border border-[rgba(231,225,216,0.92)] bg-white"
			>
				{#if compareLoading}
					<div class="flex h-full items-center justify-center">
						<Spinner className="size-4" />
					</div>
				{:else if comparison}
					<div class="flex h-full min-h-0 flex-col">
						<div
							class="flex flex-wrap items-center justify-between gap-2 border-b border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.72)] px-4 py-3"
						>
							<div class="text-sm font-semibold text-[var(--dochat-text)]">
								Rev. {comparison.base_version.revision} -> Rev. {comparison.target_version.revision}
							</div>
							<div class="flex flex-wrap items-center gap-2 text-xs text-[var(--dochat-text-soft)]">
								<span
									class="rounded-full bg-[rgba(104,159,56,0.12)] px-2 py-0.5 font-semibold text-[#4f7b2d]"
								>
									+{comparison.summary?.added_lines || 0} linhas
								</span>
								<span
									class="rounded-full bg-[rgba(179,90,90,0.12)] px-2 py-0.5 font-semibold text-[#9a4d4d]"
								>
									-{comparison.summary?.removed_lines || 0} linhas
								</span>
							</div>
						</div>

						<div class="min-h-0 overflow-auto">
							<div class="grid font-mono text-xs">
								{#each comparison.lines as line}
									<div
										class="grid grid-cols-[4rem_4rem_minmax(0,1fr)] gap-3 px-4 py-1.5 {line.type ===
										'added'
											? 'bg-[rgba(104,159,56,0.08)] text-[#305118]'
											: line.type === 'removed'
												? 'bg-[rgba(179,90,90,0.08)] text-[#7a3131]'
												: 'bg-transparent text-[var(--dochat-text-soft)]'}"
									>
										<div class="text-[0.7rem] opacity-70">{line.old_line ?? ''}</div>
										<div class="text-[0.7rem] opacity-70">{line.new_line ?? ''}</div>
										<div class="whitespace-pre-wrap break-words">
											{line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' '}
											{line.text || ' '}
										</div>
									</div>
								{/each}
							</div>
						</div>
					</div>
				{:else}
					<div
						class="flex h-full items-center justify-center text-sm text-[var(--dochat-text-soft)]"
					>
						Selecione duas versoes para comparar.
					</div>
				{/if}
			</div>
		</div>
	{/if}
</div>
