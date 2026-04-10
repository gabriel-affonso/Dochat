import { WEBUI_API_BASE_URL } from '$lib/constants';

type SearchParams = {
	query?: string | null;
	type?: string | null;
	date_from?: number | null;
	date_to?: number | null;
	collection?: string | null;
	tags?: string[] | string | null;
	source?: string | null;
	archived?: boolean | string | null;
	pinned?: boolean | string | null;
	page?: number | null;
	limit?: number | null;
};

export const searchWorkspace = async (token: string = '', params: SearchParams = {}) => {
	let error = null;
	const searchParams = new URLSearchParams();

	Object.entries(params).forEach(([key, value]) => {
		if (value === undefined || value === null || value === '') return;

		if (Array.isArray(value)) {
			if (value.length > 0) {
				searchParams.append(key, value.join(','));
			}
			return;
		}

		searchParams.append(key, String(value));
	});

	const res = await fetch(`${WEBUI_API_BASE_URL}/search?${searchParams.toString()}`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err?.detail ?? err?.message ?? 'Search request failed';
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
