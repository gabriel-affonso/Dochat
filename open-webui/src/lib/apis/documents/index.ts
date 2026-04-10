import { WEBUI_API_BASE_URL } from '$lib/constants';

type DocumentListParams = {
	query?: string | null;
	collection?: string | null;
	tags?: string[] | string | null;
	source?: string | null;
	document_type?: string | null;
	document_status?: string | null;
	locked?: boolean | null;
	archived?: boolean | null;
	page?: number | null;
	limit?: number | null;
};

type DocumentMetadataSuggestionForm = {
	instruction?: string | null;
	model?: string | null;
};

type DocumentMetadataUpdateForm = {
	title?: string | null;
	description?: string | null;
	summary?: string | null;
	tags?: string[] | null;
	entities?: string[] | null;
	author?: string | null;
	source?: string | null;
	language?: string | null;
	document_type?: string | null;
	document_status?: string | null;
	version?: string | number | null;
	suggested_collection_hints?: string[] | null;
	meta?: Record<string, any> | null;
};

type DocumentVersionCompareParams = {
	base_revision: number;
	target_revision: number;
};

const requestDocumentApi = async (token: string, path: string, options: RequestInit = {}) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/documents${path}`, {
		...options,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` }),
			...(options.headers ?? {})
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			if (err?.name === 'AbortError') {
				throw err;
			}
			error = err?.detail ?? err?.message ?? 'Documents request failed';
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getDocuments = async (token: string, params: DocumentListParams = {}) => {
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

	return requestDocumentApi(token, `/?${searchParams.toString()}`, {
		method: 'GET'
	});
};

export const getDocumentCollections = async (token: string) => {
	return requestDocumentApi(token, '/collections', {
		method: 'GET'
	});
};

export const getDocumentById = async (token: string, id: string) => {
	return requestDocumentApi(token, `/${id}`, {
		method: 'GET'
	});
};

export const getDocumentProcessing = async (token: string, id: string) => {
	return requestDocumentApi(token, `/${id}/processing`, {
		method: 'GET'
	});
};

export const getDocumentVersionsById = async (token: string, id: string) => {
	return requestDocumentApi(token, `/${id}/versions`, {
		method: 'GET'
	});
};

export const compareDocumentVersionsById = async (
	token: string,
	id: string,
	params: DocumentVersionCompareParams
) => {
	const searchParams = new URLSearchParams();
	searchParams.append('base_revision', String(params.base_revision));
	searchParams.append('target_revision', String(params.target_revision));

	return requestDocumentApi(token, `/${id}/versions/compare?${searchParams.toString()}`, {
		method: 'GET'
	});
};

export const archiveDocumentById = async (
	token: string,
	id: string,
	is_archived?: boolean | null
) => {
	return requestDocumentApi(token, `/${id}/archive`, {
		method: 'POST',
		body: JSON.stringify(is_archived === undefined || is_archived === null ? {} : { is_archived })
	});
};

export const refreshDocumentMetadataById = async (token: string, id: string) => {
	return requestDocumentApi(token, `/${id}/metadata/refresh`, {
		method: 'POST'
	});
};

export const updateDocumentMetadataById = async (
	token: string,
	id: string,
	form: DocumentMetadataUpdateForm
) => {
	return requestDocumentApi(token, `/${id}/metadata/update`, {
		method: 'POST',
		body: JSON.stringify(form)
	});
};

export const createEditableDocumentCopyById = async (token: string, id: string) => {
	return requestDocumentApi(token, `/${id}/copy`, {
		method: 'POST'
	});
};

export const suggestDocumentMetadataById = async (
	token: string,
	id: string,
	form: DocumentMetadataSuggestionForm = {}
) => {
	const controller = new AbortController();
	const timeoutId = window.setTimeout(() => controller.abort(), 60_000);

	try {
		return await requestDocumentApi(token, `/${id}/metadata/suggest`, {
			method: 'POST',
			body: JSON.stringify(form),
			signal: controller.signal
		});
	} catch (error) {
		if ((error as any)?.name === 'AbortError') {
			throw 'Metadata suggestion timed out';
		}

		throw error;
	} finally {
		window.clearTimeout(timeoutId);
	}
};

export const refreshDocumentEmbeddingsById = async (token: string, id: string) => {
	return requestDocumentApi(token, `/${id}/embeddings/refresh`, {
		method: 'POST'
	});
};

export const reprocessDocumentById = async (token: string, id: string) => {
	return requestDocumentApi(token, `/${id}/reprocess`, {
		method: 'POST'
	});
};
