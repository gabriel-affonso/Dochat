<script lang="ts">
	import { splitHighlightedText } from '$lib/utils/searchHighlight';

	export let text: string | null | undefined = '';
	export let term: string | null | undefined = null;
	export let className = 'dochat-search-highlight';

	$: segments = splitHighlightedText(text, term);
</script>

{#each segments as segment, index (`${index}-${segment.match ? '1' : '0'}-${segment.text}`)}
	{#if segment.match}
		<mark class={className}>{segment.text}</mark>
	{:else}
		{segment.text}
	{/if}
{/each}
