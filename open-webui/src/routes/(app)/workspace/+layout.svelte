<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import {
		WEBUI_NAME,
		showSidebar,
		functions,
		user,
		mobile,
		models,
		knowledge,
		tools
	} from '$lib/stores';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Sidebar from '$lib/components/icons/Sidebar.svelte';

	const i18n = getContext('i18n');

	let loaded = false;

	onMount(async () => {
		if ($user?.role !== 'admin') {
			if ($page.url.pathname.includes('/models') && !$user?.permissions?.workspace?.models) {
				goto('/');
			} else if (
				$page.url.pathname.includes('/prompts') &&
				!$user?.permissions?.workspace?.prompts
			) {
				goto('/');
			} else if ($page.url.pathname.includes('/tools') && !$user?.permissions?.workspace?.tools) {
				goto('/');
			} else if ($page.url.pathname.includes('/skills') && !$user?.permissions?.workspace?.skills) {
				goto('/');
			}
		}

		loaded = true;
	});
</script>

<svelte:head>
	<title>
		{$page.url.pathname.includes('/workspace/knowledge') ? 'Documentos' : $i18n.t('Workspace')} • {$WEBUI_NAME}
	</title>
</svelte:head>

{#if loaded}
	<div class="dochat-workspace-shell relative flex flex-col w-full min-w-0 h-full max-h-full">
		<nav class="px-2.5 pt-2 drag-region select-none">
			<div class="dochat-workspace-nav flex items-center gap-1">
				{#if $mobile}
					<div class="{$showSidebar ? 'md:hidden' : ''} self-center flex flex-none items-center">
						<Tooltip
							content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
							interactive={true}
						>
							<button
								id="sidebar-toggle-button"
								class="dochat-workspace-toggle cursor-pointer flex rounded-lg transition"
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

				<div class="min-w-0">
					<div class="dochat-workspace-kicker">
						{$page.url.pathname.includes('/workspace/knowledge') ? 'Documentos' : 'Workspace'}
					</div>
					<div
						class="flex gap-1.5 scrollbar-none overflow-x-auto w-fit text-center text-sm font-medium rounded-full bg-transparent py-1 touch-auto pointer-events-auto"
					>
						{#if $user?.role === 'admin' || $user?.permissions?.workspace?.models}
							<a
								draggable="false"
								aria-current={$page.url.pathname.includes('/workspace/models') ? 'page' : null}
								class="dochat-workspace-link min-w-fit p-1.5 {$page.url.pathname.includes(
									'/workspace/models'
								)
									? 'dochat-workspace-link-active'
									: ''} transition select-none"
								href="/workspace/models">{$i18n.t('Models')}</a
							>
						{/if}

						<a
							draggable="false"
							aria-current={$page.url.pathname.includes('/workspace/knowledge') ? 'page' : null}
							class="dochat-workspace-link min-w-fit p-1.5 {$page.url.pathname.includes(
								'/workspace/knowledge'
							)
								? 'dochat-workspace-link-active'
								: ''} transition select-none"
							href="/workspace/knowledge"
						>
							Documentos
						</a>

						{#if $user?.role === 'admin' || $user?.permissions?.workspace?.prompts}
							<a
								draggable="false"
								aria-current={$page.url.pathname.includes('/workspace/prompts') ? 'page' : null}
								class="dochat-workspace-link min-w-fit p-1.5 {$page.url.pathname.includes(
									'/workspace/prompts'
								)
									? 'dochat-workspace-link-active'
									: ''} transition select-none"
								href="/workspace/prompts">{$i18n.t('Prompts')}</a
							>
						{/if}

						{#if $user?.role === 'admin' || $user?.permissions?.workspace?.skills}
							<a
								draggable="false"
								aria-current={$page.url.pathname.includes('/workspace/skills') ? 'page' : null}
								class="dochat-workspace-link min-w-fit p-1.5 {$page.url.pathname.includes(
									'/workspace/skills'
								)
									? 'dochat-workspace-link-active'
									: ''} transition select-none"
								href="/workspace/skills"
							>
								{$i18n.t('Skills')}
							</a>
						{/if}

						{#if $user?.role === 'admin' || $user?.permissions?.workspace?.tools}
							<a
								draggable="false"
								aria-current={$page.url.pathname.includes('/workspace/tools') ? 'page' : null}
								class="dochat-workspace-link min-w-fit p-1.5 {$page.url.pathname.includes(
									'/workspace/tools'
								)
									? 'dochat-workspace-link-active'
									: ''} transition select-none"
								href="/workspace/tools"
							>
								{$i18n.t('Tools')}
							</a>
						{/if}
					</div>
				</div>

				<!-- <div class="flex items-center text-xl font-medium">{$i18n.t('Workspace')}</div> -->
			</div>
		</nav>

		<div
			class="pb-1 px-3 md:px-[18px] flex-1 min-h-0 max-h-full overflow-y-auto"
			id="workspace-container"
		>
			<slot />
		</div>
	</div>
{/if}

<style>
	.dochat-workspace-shell {
		background:
			radial-gradient(circle at top left, rgba(232, 239, 228, 0.55), transparent 28%),
			linear-gradient(180deg, rgba(251, 249, 245, 0.98), rgba(247, 244, 238, 0.96));
	}

	.dochat-workspace-nav {
		padding: 0.8rem 0.95rem;
		border: 1px solid rgba(221, 214, 202, 0.86);
		border-radius: 1.5rem;
		background: rgba(255, 255, 255, 0.82);
		box-shadow: 0 16px 36px rgba(84, 74, 58, 0.06);
		backdrop-filter: blur(16px);
	}

	.dochat-workspace-toggle {
		color: var(--dochat-text-soft, #5c5c62);
	}

	.dochat-workspace-toggle:hover {
		background: rgba(247, 244, 238, 0.96);
		color: var(--dochat-text, #1d1d1f);
	}

	.dochat-workspace-kicker {
		font-size: 0.72rem;
		font-weight: 700;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-workspace-link {
		border-radius: 9999px;
		color: var(--dochat-text-faint, #8e8e93);
	}

	.dochat-workspace-link:hover {
		color: var(--dochat-text, #1d1d1f);
		background: rgba(247, 244, 238, 0.96);
	}

	.dochat-workspace-link-active {
		background: rgba(232, 239, 228, 0.86);
		color: var(--dochat-accent, #6f8a64);
	}
</style>
