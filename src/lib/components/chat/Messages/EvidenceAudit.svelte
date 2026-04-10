<script lang="ts">
	import { getContext } from 'svelte';

	import Modal from '$lib/components/common/Modal.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n = getContext('i18n');

	type AuditNavigation = {
		document_href?: string | null;
		snippet_href?: string | null;
		document_target?: string | null;
	};

	type AuditChunk = {
		id: string;
		document_name?: string | null;
		version_label?: string | null;
		document_status?: string | null;
		document_type?: string | null;
		source_type?: string | null;
		evidence_score?: number | null;
		retrieval_score?: number | null;
		rerank_score?: number | null;
		text_excerpt?: string | null;
		collection_name?: string | null;
		page_number?: number | null;
		page_index?: number | null;
		section_label?: string | null;
		chunk_index?: number | null;
		source_reference_id?: number | null;
		file_id?: string | null;
		document_id?: string | null;
		offset_start?: number | null;
		offset_end?: number | null;
		is_used_in_answer?: boolean;
		is_cited_in_ui?: boolean;
		navigation?: AuditNavigation | null;
	};

	type AuditAlert = {
		id?: string;
		type?: string;
		title?: string;
		description?: string;
		chunk_ids?: string[];
	};

	type AuditSummary = {
		retrieved_chunks?: number;
		retrieved_documents?: number;
		used_chunks?: number;
		has_conflict?: boolean;
		has_insufficient_support?: boolean;
		grounding_confidence?: string;
	};

	type RetrievalAudit = {
		summary?: AuditSummary | null;
		chunks?: AuditChunk[] | null;
		alerts?: AuditAlert[] | null;
		selection_method?: string | null;
	};

	export let audit: RetrievalAudit | null = null;

	let show = false;
	let activeTab: 'retrieved' | 'used' | 'alerts' = 'retrieved';

	const isFinalStatus = (value: string) =>
		/(final|conclu|aprovad|vigente)/i.test(String(value ?? ''));

	const formatScore = (value: number | null | undefined) =>
		typeof value === 'number' ? value.toFixed(2) : '—';

	const formatPage = (chunk: AuditChunk) => {
		if (Number.isInteger(chunk?.page_number)) {
			return `Página ${chunk.page_number}`;
		}

		if (Number.isInteger(chunk?.page_index)) {
			return `Página ${chunk.page_index + 1}`;
		}

		return null;
	};

	const confidenceLabel = (value: string) => {
		if (value === 'high') return 'Confiança alta';
		if (value === 'medium') return 'Confiança média';
		if (value === 'low') return 'Confiança baixa';
		return 'Confiança não informada';
	};

	const buildReference = (chunk: AuditChunk) => {
		return [
			chunk?.document_name,
			chunk?.version_label,
			chunk?.document_status,
			formatPage(chunk),
			chunk?.source_type
		]
			.filter(Boolean)
			.join(' · ');
	};

	const openNavigation = (href: string | null | undefined, target = '_self') => {
		if (!href) return;
		if (target === '_self') {
			window.location.assign(href);
			return;
		}
		window.open(href, target, 'noopener,noreferrer');
	};

	const copyReference = async (chunk: AuditChunk) => {
		try {
			await navigator.clipboard.writeText(buildReference(chunk));
		} catch (error) {
			console.error(error);
		}
	};

	$: chunks = audit?.chunks ?? [];
	$: usedChunks = chunks.filter((chunk) => chunk?.is_used_in_answer);
	$: alerts = audit?.alerts ?? [];
	$: summary = audit?.summary ?? {};
	$: finalizedCount = chunks.filter((chunk) => isFinalStatus(chunk?.document_status)).length;
	$: barSummary = [
		summary?.retrieved_chunks ? `${summary.retrieved_chunks} evidências recuperadas` : null,
		summary?.retrieved_documents ? `${summary.retrieved_documents} documentos` : null,
		summary?.used_chunks ? `${summary.used_chunks} usadas na resposta` : null,
		finalizedCount ? `${finalizedCount} finalizadas` : null,
		summary?.has_conflict ? 'conflito potencial' : null,
		summary?.has_insufficient_support ? 'suporte insuficiente' : null
	]
		.filter(Boolean)
		.slice(0, 4);

	$: visibleChunks = activeTab === 'used' ? usedChunks : chunks;
</script>

{#if summary?.retrieved_chunks > 0}
	<div class="mb-3">
		<button
			type="button"
			class="w-full rounded-2xl border border-[rgba(111,138,100,0.18)] bg-[rgba(248,250,246,0.98)] px-3 py-3 text-left transition hover:border-[rgba(111,138,100,0.28)] hover:bg-[rgba(244,248,241,0.98)]"
			on:click={() => {
				show = true;
				activeTab = summary?.used_chunks ? 'used' : 'retrieved';
			}}
		>
			<div class="flex flex-wrap items-center justify-between gap-2">
				<div class="min-w-0">
					<div
						class="text-[0.68rem] uppercase tracking-[0.08em] text-[var(--dochat-accent,#6f8a64)]"
					>
						Auditoria de evidências
					</div>
					<div class="mt-1 text-sm font-medium text-[var(--dochat-text,#252118)]">
						{barSummary.join(' · ')}
					</div>
				</div>

				<div class="flex flex-wrap items-center gap-2 text-[0.72rem]">
					<span
						class="rounded-full bg-[rgba(111,138,100,0.12)] px-2.5 py-1 font-semibold text-[var(--dochat-accent,#6f8a64)]"
					>
						{confidenceLabel(summary?.grounding_confidence)}
					</span>

					{#if summary?.has_conflict}
						<span
							class="rounded-full bg-[rgba(179,90,90,0.12)] px-2.5 py-1 font-semibold text-[#9a4d4d]"
						>
							Conflito
						</span>
					{/if}

					{#if summary?.has_insufficient_support}
						<span
							class="rounded-full bg-[rgba(195,138,61,0.14)] px-2.5 py-1 font-semibold text-[#9a6a2d]"
						>
							Parcial
						</span>
					{/if}

					<span
						class="rounded-full border border-[rgba(111,138,100,0.18)] px-2.5 py-1 font-semibold text-[var(--dochat-accent,#6f8a64)]"
					>
						Ver evidências
					</span>
				</div>
			</div>
		</button>
	</div>

	<Modal size="xl" bind:show>
		<div class="max-h-[80vh]">
			<div class="flex items-center justify-between px-5 pt-4 pb-2">
				<div>
					<div class="text-lg font-semibold text-[var(--dochat-text,#252118)]">
						Auditoria de evidências
					</div>
					<div class="mt-1 text-sm text-[var(--dochat-text-soft,#6b665e)]">
						Recuperado, priorizado e exibido para sustentar a resposta.
					</div>
				</div>

				<button
					type="button"
					class="rounded-full p-1 text-[var(--dochat-text-soft,#6b665e)] transition hover:bg-black/5"
					aria-label={$i18n.t('Close')}
					on:click={() => {
						show = false;
					}}
				>
					<XMark className="size-5" />
				</button>
			</div>

			<div class="px-5 pb-4">
				<div class="flex flex-wrap gap-2">
					<button
						type="button"
						class={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
							activeTab === 'retrieved'
								? 'bg-[var(--dochat-accent-soft,#e8efe4)] text-[var(--dochat-accent,#6f8a64)]'
								: 'border border-[rgba(111,138,100,0.18)] text-[var(--dochat-text-soft,#6b665e)]'
						}`}
						on:click={() => {
							activeTab = 'retrieved';
						}}
					>
						Recuperado ({summary?.retrieved_chunks ?? 0})
					</button>
					<button
						type="button"
						class={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
							activeTab === 'used'
								? 'bg-[var(--dochat-accent-soft,#e8efe4)] text-[var(--dochat-accent,#6f8a64)]'
								: 'border border-[rgba(111,138,100,0.18)] text-[var(--dochat-text-soft,#6b665e)]'
						}`}
						on:click={() => {
							activeTab = 'used';
						}}
					>
						Usado na resposta ({summary?.used_chunks ?? 0})
					</button>
					<button
						type="button"
						class={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
							activeTab === 'alerts'
								? 'bg-[var(--dochat-accent-soft,#e8efe4)] text-[var(--dochat-accent,#6f8a64)]'
								: 'border border-[rgba(111,138,100,0.18)] text-[var(--dochat-text-soft,#6b665e)]'
						}`}
						on:click={() => {
							activeTab = 'alerts';
						}}
					>
						Conflitos e lacunas ({alerts.length})
					</button>
				</div>
			</div>

			<div class="overflow-y-auto px-5 pb-5">
				{#if activeTab === 'alerts'}
					{#if alerts.length === 0}
						<div
							class="rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.72)] px-4 py-5 text-sm text-[var(--dochat-text-soft,#6b665e)]"
						>
							Nenhum alerta adicional foi identificado nesta resposta.
						</div>
					{:else}
						<div class="space-y-3">
							{#each alerts as alert}
								<div
									class={`rounded-3xl border px-4 py-4 ${
										alert?.type === 'conflict'
											? 'border-[rgba(179,90,90,0.22)] bg-[rgba(255,245,245,0.82)]'
											: 'border-[rgba(195,138,61,0.22)] bg-[rgba(255,249,240,0.85)]'
									}`}
								>
									<div class="text-sm font-semibold text-[var(--dochat-text,#252118)]">
										{alert?.title}
									</div>
									<div class="mt-1 text-sm text-[var(--dochat-text-soft,#6b665e)]">
										{alert?.description}
									</div>
									{#if alert?.chunk_ids?.length}
										<div class="mt-3 flex flex-wrap gap-2">
											{#each alert.chunk_ids as chunkId}
												{@const linkedChunk = chunks.find((chunk) => chunk.id === chunkId)}
												{#if linkedChunk}
													<button
														type="button"
														class="rounded-full border border-[rgba(111,138,100,0.18)] px-2.5 py-1 text-xs font-semibold text-[var(--dochat-accent,#6f8a64)]"
														on:click={() => {
															activeTab = 'retrieved';
														}}
													>
														{linkedChunk.document_name}
													</button>
												{/if}
											{/each}
										</div>
									{/if}
								</div>
							{/each}
						</div>
					{/if}
				{:else if visibleChunks.length === 0}
					<div
						class="rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.72)] px-4 py-5 text-sm text-[var(--dochat-text-soft,#6b665e)]"
					>
						Nenhuma evidência foi classificada nesta aba.
					</div>
				{:else}
					<div class="space-y-3">
						{#each visibleChunks as chunk}
							<div class="rounded-3xl border border-[rgba(231,225,216,0.92)] bg-white px-4 py-4">
								<div class="flex flex-wrap items-start justify-between gap-3">
									<div class="min-w-0">
										<div class="text-sm font-semibold text-[var(--dochat-text,#252118)]">
											{chunk.document_name}
										</div>
										<div class="mt-2 flex flex-wrap gap-2 text-[0.7rem]">
											{#if chunk.version_label}
												<span
													class="rounded-full bg-[rgba(111,138,100,0.12)] px-2 py-1 font-semibold text-[var(--dochat-accent,#6f8a64)]"
												>
													{chunk.version_label}
												</span>
											{/if}
											{#if chunk.document_status}
												<span
													class="rounded-full bg-[rgba(20,20,20,0.05)] px-2 py-1 font-semibold text-[var(--dochat-text-soft,#6b665e)]"
												>
													{chunk.document_status}
												</span>
											{/if}
											{#if chunk.document_type}
												<span
													class="rounded-full bg-[rgba(20,20,20,0.05)] px-2 py-1 font-semibold text-[var(--dochat-text-soft,#6b665e)]"
												>
													{chunk.document_type}
												</span>
											{/if}
											{#if chunk.source_type}
												<span
													class="rounded-full bg-[rgba(20,20,20,0.05)] px-2 py-1 font-semibold text-[var(--dochat-text-soft,#6b665e)]"
												>
													{chunk.source_type}
												</span>
											{/if}
											{#if chunk.is_used_in_answer}
												<span
													class="rounded-full bg-[rgba(111,138,100,0.12)] px-2 py-1 font-semibold text-[var(--dochat-accent,#6f8a64)]"
												>
													Usado na resposta
												</span>
											{/if}
											{#if chunk.is_cited_in_ui}
												<span
													class="rounded-full bg-[rgba(83,124,204,0.12)] px-2 py-1 font-semibold text-[#4f6ca8]"
												>
													Evidência principal
												</span>
											{/if}
										</div>
									</div>

									<div
										class="rounded-2xl bg-[rgba(251,249,245,0.9)] px-3 py-2 text-xs text-[var(--dochat-text-soft,#6b665e)]"
									>
										<div>Score de evidência: {formatScore(chunk.evidence_score)}</div>
										<div>Retrieval: {formatScore(chunk.retrieval_score)}</div>
										<div>Rerank: {formatScore(chunk.rerank_score)}</div>
									</div>
								</div>

								<div
									class="mt-3 rounded-2xl bg-[rgba(251,249,245,0.82)] px-3 py-3 text-sm leading-6 text-[var(--dochat-text,#252118)]"
								>
									{chunk.text_excerpt}
								</div>

								<div
									class="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-[var(--dochat-text-soft,#6b665e)]"
								>
									{#if chunk.collection_name}
										<span>Coleção: {chunk.collection_name}</span>
									{/if}
									{#if formatPage(chunk)}
										<span>{formatPage(chunk)}</span>
									{/if}
									{#if chunk.section_label}
										<span>Seção: {chunk.section_label}</span>
									{/if}
									{#if Number.isInteger(chunk.chunk_index)}
										<span>Chunk: {chunk.chunk_index}</span>
									{/if}
									<span>Ref. [{chunk.source_reference_id}]</span>
								</div>

								<div class="mt-4 flex flex-wrap gap-2">
									{#if chunk?.navigation?.document_href}
										<button
											type="button"
											class="rounded-full bg-[var(--dochat-accent,#6f8a64)] px-3 py-1.5 text-xs font-semibold text-white"
											on:click={() =>
												openNavigation(
													chunk?.navigation?.document_href,
													chunk?.navigation?.document_target ?? '_self'
												)}
										>
											Abrir documento
										</button>
									{/if}
									{#if chunk?.navigation?.snippet_href}
										<button
											type="button"
											class="rounded-full border border-[rgba(111,138,100,0.18)] px-3 py-1.5 text-xs font-semibold text-[var(--dochat-accent,#6f8a64)]"
											on:click={() =>
												openNavigation(
													chunk?.navigation?.snippet_href,
													chunk?.navigation?.document_target ?? '_self'
												)}
										>
											Ir para o trecho
										</button>
									{/if}
									<button
										type="button"
										class="rounded-full border border-[rgba(111,138,100,0.18)] px-3 py-1.5 text-xs font-semibold text-[var(--dochat-accent,#6f8a64)]"
										on:click={() => copyReference(chunk)}
									>
										Copiar referência
									</button>
								</div>

								<details
									class="mt-3 rounded-2xl bg-[rgba(251,249,245,0.72)] px-3 py-2 text-xs text-[var(--dochat-text-soft,#6b665e)]"
								>
									<summary class="cursor-pointer font-semibold"> Metadados técnicos </summary>
									<div class="mt-2 grid gap-1">
										<div>Arquivo: {chunk.file_id ?? '—'}</div>
										<div>Documento: {chunk.document_id ?? '—'}</div>
										<div>Offset: {chunk.offset_start ?? '—'} → {chunk.offset_end ?? '—'}</div>
										<div>Método: {audit?.selection_method ?? '—'}</div>
									</div>
								</details>
							</div>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	</Modal>
{/if}
