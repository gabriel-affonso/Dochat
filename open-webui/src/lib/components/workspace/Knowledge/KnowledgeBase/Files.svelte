<script lang="ts">
	import dayjs from '$lib/dayjs';
	import duration from 'dayjs/plugin/duration';
	import relativeTime from 'dayjs/plugin/relativeTime';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18nType } from 'i18next';

	dayjs.extend(duration);
	dayjs.extend(relativeTime);

	import { getContext } from 'svelte';
	const i18n = getContext<Writable<I18nType>>('i18n');

	import { formatFileSize } from '$lib/utils';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import DocumentPage from '$lib/components/icons/DocumentPage.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	type KnowledgeLike = {
		write_access?: boolean;
	};

	type FileItem = {
		id?: string | null;
		itemId?: string | null;
		tempId?: string | null;
		name?: string | null;
		filename?: string | null;
		meta?: Record<string, any> | null;
		data?: Record<string, any> | null;
		user?: Record<string, any> | null;
		status?: string | null;
		updated_at?: number | null;
		created_at?: number | null;
	};

	export let knowledge: KnowledgeLike | null = null;
	export let selectedFileId: string | null = null;
	export let files: FileItem[] = [];
	export let selectedDocumentIds: string[] = [];

	export let onClick: (fileId: string | null | undefined) => void = () => {};
	export let onDelete: (fileId: string | null | undefined) => void = () => {};
	export let onToggleContext: (fileId: string | null | undefined) => void = () => {};

	const getActorLabel = (actor: Record<string, any> | null | undefined) =>
		actor?.name || actor?.email || actor?.id || '—';
	const getUploadActor = (file: FileItem) => getActorLabel(file?.meta?.uploaded_by || file?.user);
	const getModifiedActor = (file: FileItem) =>
		getActorLabel(file?.meta?.last_modified_by || file?.meta?.uploaded_by || file?.user);
	const getLastModifiedAt = (file: FileItem) =>
		file?.meta?.last_modified_at || file?.updated_at || file?.created_at || null;
	const getVersionControl = (file: FileItem) =>
		file?.meta?.version_control && typeof file.meta.version_control === 'object'
			? file.meta.version_control
			: { status: 'available', revision: Number(file?.meta?.version || 1) || 1 };
	const getDocumentType = (file: FileItem) =>
		file?.meta?.document_type || file?.meta?.content_type || 'Documento';
	const getDocumentStatus = (file: FileItem) => file?.meta?.document_status || 'Em elaboração';
	const isLockedByStatus = (file: FileItem) => Boolean(file?.meta?.is_locked_by_status);
	const getStatusLabel = (file: FileItem) => {
		if (getVersionControl(file)?.status === 'checked_out') {
			return `Check-out: ${getActorLabel(getVersionControl(file)?.checked_out_by)}`;
		}

		return file?.status || file?.data?.processing_status || file?.data?.status || 'ready';
	};
</script>

<div class=" max-h-full flex flex-col w-full gap-[0.5px]">
	{#each files as file (file?.id ?? file?.itemId ?? file?.tempId)}
		<div
			class=" flex cursor-pointer w-full px-1.5 py-0.5 bg-transparent dark:hover:bg-gray-850/50 hover:bg-white rounded-xl transition {selectedFileId
				? ''
				: 'hover:bg-gray-100 dark:hover:bg-gray-850'}"
		>
			<div class="flex items-center px-2">
				<input
					type="checkbox"
					checked={selectedDocumentIds.includes(file?.id ?? '')}
					disabled={!file?.id}
					aria-label={`Selecionar ${file?.name ?? file?.filename ?? file?.meta?.name ?? 'Documento'} para conversa`}
					on:change|stopPropagation={() => onToggleContext(file?.id)}
				/>
			</div>
			<button
				class="relative group flex items-center gap-1 rounded-xl p-2 text-left flex-1 justify-between"
				type="button"
				on:click={async () => {
					console.log(file);
					onClick(file?.id ?? file?.tempId);
				}}
			>
				<div class="min-w-0">
					<div class="flex gap-2 items-center line-clamp-1">
						<div class="shrink-0">
							{#if file?.status !== 'uploading'}
								<DocumentPage className="size-3.5" />
							{:else}
								<Spinner className="size-3.5" />
							{/if}
						</div>

						<div class="line-clamp-1 text-sm">
							{file?.name ?? file?.filename ?? file?.meta?.name ?? 'Documento'}
							{#if file?.meta?.size}
								<span class="text-xs text-gray-500">{formatFileSize(file?.meta?.size)}</span>
							{/if}
						</div>
					</div>
					<div
						class="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.68rem] text-gray-500"
					>
						<span>Tipologia: {getDocumentType(file)}</span>
						<span>Status: {getDocumentStatus(file)}</span>
						{#if isLockedByStatus(file)}
							<span class="font-semibold text-[#9a4d4d]">Travado</span>
						{/if}
						<span>Upload: {getUploadActor(file)}</span>
						<span>Alterado: {getModifiedActor(file)}</span>
						<span>Estado: {getStatusLabel(file)}</span>
					</div>
				</div>

				<div class="flex items-center gap-2 shrink-0">
					{#if getLastModifiedAt(file)}
						<Tooltip content={dayjs(getLastModifiedAt(file) * 1000).format('LLLL')}>
							<div>
								{dayjs(getLastModifiedAt(file) * 1000).fromNow()}
							</div>
						</Tooltip>
					{/if}
				</div>
			</button>

			{#if knowledge?.write_access}
				<div class="flex items-center">
					<Tooltip content={$i18n.t('Delete')}>
						<button
							class="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-850 transition"
							type="button"
							on:click={() => {
								onDelete(file?.id ?? file?.tempId);
							}}
						>
							<XMark />
						</button>
					</Tooltip>
				</div>
			{/if}
		</div>
	{/each}
</div>
