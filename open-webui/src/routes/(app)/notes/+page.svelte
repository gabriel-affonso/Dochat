<script>
	import { getContext, onMount } from 'svelte';

	const i18n = getContext('i18n');

	import dayjs from '$lib/dayjs';
	import { mobile, showSidebar, user } from '$lib/stores';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { createNoteHandler } from '$lib/components/notes/utils';

	import UserMenu from '$lib/components/layout/Sidebar/UserMenu.svelte';
	import Notes from '$lib/components/notes/Notes.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	let loaded = false;

	onMount(async () => {
		if (
			$page.url.searchParams.get('content') !== null ||
			$page.url.searchParams.get('title') !== null
		) {
			const title = $page.url.searchParams.get('title') ?? dayjs().format('YYYY-MM-DD');
			const content = $page.url.searchParams.get('content') ?? '';

			const res = await createNoteHandler(title, content);

			if (res) {
				goto(`/notes/${res.id}`);
			}
			return;
		}

		loaded = true;
	});
</script>

{#if loaded}
	<div
		class="dochat-notes-shell flex flex-col w-full min-w-0 h-full max-h-full"
	>
		<nav class="px-2 pt-2 w-full drag-region">
			<div class="dochat-notes-nav flex items-center">
				{#if $mobile}
					<div class="{$showSidebar ? 'md:hidden' : ''} flex flex-none items-center">
						<Tooltip
							content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
							interactive={true}
						>
							<button
								id="sidebar-toggle-button"
								class="dochat-notes-toggle cursor-pointer flex rounded-lg transition"
								aria-label={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
								on:click={() => {
									showSidebar.set(!$showSidebar);
								}}
							>
								<div class=" self-center p-1.5">
									<Sidebar />
								</div>
							</button>
						</Tooltip>
					</div>
				{/if}

				<div class="ml-2 py-0.5 self-center flex items-center justify-between w-full gap-3">
					<div class="min-w-0">
						<div class="dochat-notes-kicker">Notas</div>
						<div class="dochat-notes-title">Memoria de trabalho</div>
						<div class="dochat-notes-subtitle">
							Escreva, recupere e reorganize pensamentos sem sair do workspace.
						</div>
					</div>

					<div class=" self-center flex items-center gap-1">
						{#if $user !== undefined && $user !== null}
							<UserMenu
								className="max-w-[240px]"
								role={$user?.role}
								help={true}
								on:show={(e) => {
									if (e.detail === 'archived-chat') {
										goto('/archive');
									}
								}}
								>
								<button class="dochat-notes-user select-none flex w-full" aria-label="User Menu">
									<div class=" self-center">
										<img
											src={`${WEBUI_API_BASE_URL}/users/${$user?.id}/profile/image`}
											class="size-6 object-cover rounded-full"
											alt="User profile"
											draggable="false"
										/>
									</div>
								</button>
							</UserMenu>
						{/if}
					</div>
				</div>
			</div>
		</nav>

		<div class=" flex-1 min-h-0 max-h-full overflow-y-auto @container">
			<Notes />
		</div>
	</div>
{/if}

<style>
	.dochat-notes-shell {
		background:
			radial-gradient(circle at top left, rgba(255, 247, 224, 0.45), transparent 26%),
			linear-gradient(180deg, rgba(251, 249, 245, 0.98), rgba(247, 244, 238, 0.96));
	}

	.dochat-notes-nav {
		padding: 0.85rem 0.95rem;
		border: 1px solid rgba(221, 214, 202, 0.86);
		border-radius: 1.5rem;
		background: rgba(255, 255, 255, 0.84);
		box-shadow: 0 16px 36px rgba(84, 74, 58, 0.06);
		backdrop-filter: blur(16px);
	}

	.dochat-notes-toggle {
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-notes-toggle:hover {
		background: rgba(247, 244, 238, 0.96);
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-notes-kicker {
		font-size: 0.72rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: #8a6d2e;
	}

	.dochat-notes-title {
		font-size: 1.1rem;
		font-weight: 700;
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-notes-subtitle {
		font-size: 0.84rem;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-notes-user {
		padding: 0.25rem;
		border-radius: 9999px;
	}

	.dochat-notes-user:hover {
		background: rgba(247, 244, 238, 0.96);
	}
</style>
