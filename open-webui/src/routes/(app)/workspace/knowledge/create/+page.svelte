<script lang="ts">
	import { goto } from '$app/navigation';
	import { user } from '$lib/stores';
	import { onMount } from 'svelte';
	import CreateKnowledgeBase from '$lib/components/workspace/Knowledge/CreateKnowledgeBase.svelte';

	let canCreate = false;

	onMount(async () => {
		if ($user?.role !== 'admin') {
			await goto('/workspace/knowledge');
			return;
		}

		canCreate = true;
	});
</script>

{#if canCreate}
	<CreateKnowledgeBase />
{/if}
