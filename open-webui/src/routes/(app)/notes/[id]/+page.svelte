<script lang="ts">
	import { onMount } from 'svelte';

	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { showSidebar } from '$lib/stores';

	import dayjs from '$lib/dayjs';
	import { createNoteHandler } from '$lib/components/notes/utils';

	import NoteEditor from '$lib/components/notes/NoteEditor.svelte';

	let loaded = false;

	onMount(async () => {
		loaded = true;
	});
</script>

{#if loaded}
	<div
		id="note-container"
		class="dochat-note-editor-shell w-full min-w-0 h-full"
	>
		<NoteEditor id={$page.params.id} />
	</div>
{/if}

<style>
	.dochat-note-editor-shell {
		background:
			radial-gradient(circle at top left, rgba(255, 247, 224, 0.42), transparent 26%),
			linear-gradient(180deg, rgba(251, 249, 245, 0.98), rgba(247, 244, 238, 0.96));
	}
</style>
