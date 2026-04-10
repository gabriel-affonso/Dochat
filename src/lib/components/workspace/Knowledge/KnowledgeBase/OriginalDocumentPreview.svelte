<script lang="ts">
	import { onDestroy } from 'svelte';

	import { getFileContentBlobById } from '$lib/apis/files';
	import Spinner from '$lib/components/common/Spinner.svelte';

	type PreviewFile = {
		id?: string | null;
		filename?: string | null;
		name?: string | null;
		meta?: Record<string, unknown> | null;
	};

	export let token = '';
	export let file: PreviewFile | null = null;
	export let active = false;
	export let initialPage: number | null = null;

	let loading = false;
	let error = '';
	let objectUrl = '';
	let textContent = '';
	let previewContentType = '';
	let loadedFileId: string | null = null;

	const getFileLabel = (fileItem: PreviewFile | null) =>
		fileItem?.filename || fileItem?.name || fileItem?.meta?.name || 'Documento';

	const cleanupObjectUrl = () => {
		if (objectUrl) {
			URL.revokeObjectURL(objectUrl);
			objectUrl = '';
		}
	};

	const isPdf = (fileItem: PreviewFile | null, contentType: string) =>
		contentType === 'application/pdf' ||
		String(fileItem?.meta?.content_type || '')
			.toLowerCase()
			.includes('pdf') ||
		getFileLabel(fileItem).toLowerCase().endsWith('.pdf');

	const isImage = (fileItem: PreviewFile | null, contentType: string) =>
		contentType.startsWith('image/') ||
		String(fileItem?.meta?.content_type || '').startsWith('image/');

	const isTextLike = (fileItem: PreviewFile | null, contentType: string) =>
		contentType.startsWith('text/') ||
		[
			'application/json',
			'application/xml',
			'application/javascript',
			'application/x-yaml',
			'text/markdown'
		].includes(contentType) ||
		['.md', '.txt', '.json', '.csv', '.xml', '.html'].some((suffix) =>
			getFileLabel(fileItem).toLowerCase().endsWith(suffix)
		);

	const triggerDownload = () => {
		if (!objectUrl) return;
		const link = document.createElement('a');
		link.href = objectUrl;
		link.download = getFileLabel(file);
		document.body.appendChild(link);
		link.click();
		document.body.removeChild(link);
	};

	const openOriginal = () => {
		if (!objectUrl) return;
		const url = initialPage ? `${objectUrl}#page=${initialPage}` : objectUrl;
		window.open(url, '_blank', 'noopener,noreferrer');
	};

	const loadOriginalPreview = async (fileId: string) => {
		if (!token || !fileId) return;

		loading = true;
		error = '';
		textContent = '';
		cleanupObjectUrl();

		try {
			const res = await getFileContentBlobById(token, fileId);
			if (loadedFileId !== fileId && loadedFileId !== null) {
				loading = false;
				return;
			}
			if (!res) return;

			const contentType = res.contentType || file?.meta?.content_type || '';
			previewContentType = contentType;
			objectUrl = URL.createObjectURL(res.blob);

			if (isTextLike(file, contentType)) {
				textContent = await res.blob.text();
			}
		} catch (err) {
			console.error(err);
			error = String(err || 'Nao foi possivel carregar o arquivo original.');
		} finally {
			loading = false;
		}
	};

	$: if (active && file?.id && loadedFileId !== file.id) {
		loadedFileId = file.id;
		void loadOriginalPreview(file.id);
	}

	onDestroy(() => {
		cleanupObjectUrl();
	});
</script>

<div class="flex h-full min-h-0 flex-col gap-3">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<div class="text-xs text-[var(--dochat-text-soft)]">
			Arquivo original: {getFileLabel(file)}
		</div>
		<div class="flex items-center gap-2">
			<button
				type="button"
				class="rounded-full bg-[var(--dochat-accent-soft)] px-3 py-1.5 text-xs font-semibold text-[var(--dochat-accent)]"
				on:click={openOriginal}
				disabled={!objectUrl}
			>
				Abrir original
			</button>
			<button
				type="button"
				class="rounded-full border border-[rgba(111,138,100,0.18)] px-3 py-1.5 text-xs font-semibold text-[var(--dochat-accent)]"
				on:click={triggerDownload}
				disabled={!objectUrl}
			>
				Baixar
			</button>
		</div>
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
	{:else if objectUrl}
		{#if isPdf(file, previewContentType)}
			<iframe
				src={initialPage ? `${objectUrl}#page=${initialPage}` : objectUrl}
				title={`Original de ${getFileLabel(file)}`}
				class="h-full min-h-[28rem] w-full rounded-3xl border border-[rgba(231,225,216,0.92)] bg-white"
			></iframe>
		{:else if isImage(file, previewContentType)}
			<div
				class="flex h-full min-h-[28rem] items-center justify-center overflow-hidden rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.72)] p-4"
			>
				<img
					src={objectUrl}
					alt={getFileLabel(file)}
					class="max-h-full max-w-full rounded-2xl object-contain"
				/>
			</div>
		{:else if textContent}
			<pre
				class="h-full min-h-[28rem] overflow-auto whitespace-pre-wrap rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.72)] p-4 text-xs text-[var(--dochat-text)]">{textContent}</pre>
		{:else}
			<div
				class="flex h-full min-h-[28rem] flex-col items-center justify-center gap-3 rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.72)] p-6 text-center"
			>
				<div class="text-sm font-semibold text-[var(--dochat-text)]">
					Pre-visualizacao inline indisponivel para este tipo de arquivo.
				</div>
				<div class="max-w-md text-xs text-[var(--dochat-text-soft)]">
					O original continua disponivel para abrir numa nova aba ou descarregar.
				</div>
			</div>
		{/if}
	{:else}
		<div
			class="flex h-full min-h-[28rem] items-center justify-center rounded-3xl border border-[rgba(231,225,216,0.92)] bg-[rgba(251,249,245,0.72)] text-sm text-[var(--dochat-text-soft)]"
		>
			Selecione um documento para carregar o original.
		</div>
	{/if}
</div>
