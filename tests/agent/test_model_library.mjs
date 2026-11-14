import test from "node:test";
import assert from "node:assert/strict";

import {
  summarizeBundleMatches,
  calculateProgress,
  formatBytes,
  fetchRepoInfo,
  matchBundles,
  visionProjectorFiles,
  listInstalledModels,
  deleteInstalledModel,
  cancelModelDownload,
  downloadModelBundle,
  activateModel,
} from "../../apps/desktop/workbench/src/settings/lib/modelLibrary.ts";

test("summarizeBundleMatches calculates correct file sizes and detects unknown sizes", () => {
  const repoFiles = [
    { path: "model-q4_k_m.gguf", size: 1024 * 1024 * 500, sha256: "abc" },
    { path: "mmproj-model-f16.gguf", size: 1024 * 1024 * 50, sha256: "def" },
    { path: "unknown.gguf", size: null, sha256: null },
  ];

  const matches = [
    {
      descriptor_id: "llm-gguf",
      kind: "llm",
      files: ["model-q4_k_m.gguf"],
    },
    {
      descriptor_id: "vision-gguf-mmproj",
      kind: "vision",
      files: ["model-q4_k_m.gguf", "mmproj-model-f16.gguf"],
    },
    {
      descriptor_id: "llm-gguf",
      kind: "llm",
      files: ["unknown.gguf"],
    },
  ];

  const summaries = summarizeBundleMatches(matches, repoFiles);

  assert.equal(summaries.length, 3);

  // Variant 1
  assert.equal(summaries[0].descriptorId, "llm-gguf");
  assert.equal(summaries[0].totalBytes, 1024 * 1024 * 500);
  assert.equal(summaries[0].hasUnknownSize, false);

  // Variant 2 (multimodal)
  assert.equal(summaries[1].descriptorId, "vision-gguf-mmproj");
  assert.equal(summaries[1].totalBytes, 1024 * 1024 * 550);
  assert.equal(summaries[1].hasUnknownSize, false);

  // Variant 3 (unknown size)
  assert.equal(summaries[2].descriptorId, "llm-gguf");
  assert.equal(summaries[2].totalBytes, 0);
  assert.equal(summaries[2].hasUnknownSize, true);
});

test("formatBytes produces human readable strings", () => {
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(1024), "1.00 KB");
  assert.equal(formatBytes(1024 * 1024 * 1.5), "1.50 MB");
  assert.equal(formatBytes(1024 * 1024 * 1024 * 2.25), "2.25 GB");
});

test("calculateProgress calculates bounds safely", () => {
  assert.equal(calculateProgress(0, 100), 0);
  assert.equal(calculateProgress(50, 100), 50);
  assert.equal(calculateProgress(100, 100), 100);
  assert.equal(calculateProgress(150, 100), 100);
  assert.equal(calculateProgress(50, 0), 0);
});

test("metadata/variant selection via fetchRepoInfo and matchBundles", async () => {
  const mockRepoInfo = {
    repo_id: "test-org/test-model",
    revision: "main123",
    gated: false,
    files: [
      { path: "model-q4.gguf", size: 2000 },
      { path: "README.md", size: 50 },
    ],
    total_size: 2050,
  };

  const mockBundles = [
    {
      descriptor_id: "llm-gguf",
      kind: "llm",
      files: ["model-q4.gguf"],
    },
  ];

  const mockInvoke = async (cmd, args) => {
    if (cmd === "model_fetch_repo_info") {
      assert.equal(args.repoId, "test-org/test-model");
      assert.equal(args.revision, "main123");
      return mockRepoInfo;
    }
    if (cmd === "model_match_bundles") {
      assert.equal(args.kind, "llm");
      assert.deepEqual(args.files, mockRepoInfo.files);
      return mockBundles;
    }
    throw new Error(`Unexpected command: ${cmd}`);
  };

  const info = await fetchRepoInfo("test-org/test-model", "main123", { invoke: mockInvoke });
  assert.equal(info.repo_id, "test-org/test-model");

  const matches = await matchBundles("llm", info.files, { invoke: mockInvoke });
  const summaries = summarizeBundleMatches(matches, info.files);
  assert.equal(summaries.length, 1);
  assert.equal(summaries[0].descriptorId, "llm-gguf");
  assert.equal(summaries[0].totalBytes, 2000);
});

test("cancel before progress properly sends model_download_cancel", async () => {
  let cancelCalled = false;
  const mockInvoke = async (cmd, args) => {
    if (cmd === "model_download_cancel") {
      assert.equal(args.taskId, "task-cancel-1");
      cancelCalled = true;
      return true;
    }
    throw new Error(`Unexpected command: ${cmd}`);
  };

  const res = await cancelModelDownload("task-cancel-1", { invoke: mockInvoke });
  assert.equal(res, true);
  assert.equal(cancelCalled, true);
});

test("other request events ignored and unsubscribe called on success", async () => {
  let unsubscribed = false;
  let registeredListener = null;

  const mockListen = async (event, handler) => {
    assert.equal(event, "model-download:progress");
    registeredListener = handler;
    return () => {
      unsubscribed = true;
    };
  };

  const mockInvoke = async (cmd, args) => {
    if (cmd === "model_download_start") {
      assert.equal(args.request.request_id, "my-req-123");
      // Simulate progress events while download runs
      if (registeredListener) {
        // Event from another request (should be ignored)
        registeredListener({
          payload: {
            task_id: "other-req",
            file: "other.gguf",
            file_index: 0,
            file_count: 1,
            downloaded: 10,
            total: 100,
          },
        });
        // Event for this request
        registeredListener({
          payload: {
            task_id: "my-req-123",
            file: "model.gguf",
            file_index: 0,
            file_count: 1,
            downloaded: 50,
            total: 100,
          },
        });
      }
      return {
        owner: "test",
        complete: true,
        repo_id: "org/repo",
        revision: "rev1",
        kind: "llm",
        bundle_id: "llm-gguf",
        files: [],
      };
    }
    throw new Error(`Unexpected command: ${cmd}`);
  };

  const progressUpdates = [];
  const manifest = await downloadModelBundle(
    {
      kind: "llm",
      repoId: "org/repo",
      revision: "rev1",
      descriptorId: "llm-gguf",
      files: ["model.gguf"],
      requestId: "my-req-123",
    },
    (p) => {
      progressUpdates.push(p);
    },
    { invoke: mockInvoke, listen: mockListen },
  );

  assert.equal(manifest.repo_id, "org/repo");
  assert.equal(progressUpdates.length, 1);
  assert.equal(progressUpdates[0].task_id, "my-req-123");
  assert.equal(progressUpdates[0].downloaded, 50);
  assert.equal(unsubscribed, true, "Progress listener was uninstalled after completion");
});

test("download error releases listener and propagates failure", async () => {
  let unsubscribed = false;

  const mockListen = async (event) => {
    return () => {
      unsubscribed = true;
    };
  };

  const mockInvoke = async (cmd) => {
    if (cmd === "model_download_start") {
      throw new Error("Disk full or network timeout");
    }
    throw new Error(`Unexpected command: ${cmd}`);
  };

  await assert.rejects(
    async () => {
      await downloadModelBundle(
        {
          kind: "llm",
          repoId: "org/repo",
          revision: "rev1",
          descriptorId: "llm-gguf",
          files: ["model.gguf"],
        },
        () => {},
        { invoke: mockInvoke, listen: mockListen },
      );
    },
    { message: "Disk full or network timeout" },
  );

  assert.equal(unsubscribed, true, "Listener must be released on error");
});

test("on-demand managed activation selects its stable identity without changing custom endpoints", async () => {
  let endpointsSaved = null;
  let defaultModelSaved = null;

  const mockInvoke = async (cmd, args) => {
    if (cmd === "backend" && args.method === "models.activate") {
      // Lazy on-demand activation returns configured: true, running: false
      return {
        kind: "llm",
        baseURL: "http://127.0.0.1:8080/v1",
        modelId: `jarvis-managed:llm:test/model@${"a".repeat(40)}`,
        context: 16384,
        configured: true,
        running: false,
      };
    }
    throw new Error(`Unexpected command: ${cmd}`);
  };

  const existingEndpoints = [
    {
      id: "other-endpoint",
      name: "Other",
      baseURL: "http://example.com/v1",
      modelId: "gpt-4",
      contextLimit: 4096,
    },
  ];

  const deps = {
    invoke: mockInvoke,
    listen: async () => () => {},
    getCustomEndpoints: () => existingEndpoints,
    setCustomEndpoints: (endpoints) => {
      endpointsSaved = endpoints;
    },
    setDefaultModel: (modelId) => {
      defaultModelSaved = modelId;
    },
  };

  const result = await activateModel("llm", "test/model", "a".repeat(40), "vulkan", deps);
  assert.equal(result.configured, true);
  assert.equal(result.running, false);

  assert.equal(endpointsSaved, null, "Activation must not mutate a shared custom endpoint");
  assert.equal(defaultModelSaved, `jarvis-managed:llm:test/model@${"a".repeat(40)}`);
});

test("listInstalledModels and deleteInstalledModel work via invoke", async () => {
  const installedList = [
    {
      owner: "local",
      complete: true,
      repo_id: "test/repo",
      revision: "123",
      kind: "llm",
      bundle_id: "bundle1",
      files: [],
    },
  ];
  let deletedArgs = null;

  const mockInvoke = async (cmd, args) => {
    if (cmd === "model_list_installed") {
      return installedList;
    }
    if (cmd === "model_delete") {
      deletedArgs = args;
      return null;
    }
    throw new Error(`Unexpected command: ${cmd}`);
  };

  const list = await listInstalledModels({ invoke: mockInvoke });
  assert.deepEqual(list, installedList);

  await deleteInstalledModel("llm", "test/repo", "123", { invoke: mockInvoke });
  assert.deepEqual(deletedArgs, {
    kind: "llm",
    repoId: "test/repo",
    revision: "123",
  });
});


test("managed activation rejects a mismatched bundle identity before changing preferences", async () => {
  let preferenceChanges = 0;
  const deps = {
    invoke: async () => ({ configured: true, modelId: "jarvis-managed:llm:other/model@rev2" }),
    setDefaultModel: () => { preferenceChanges++; },
    setCustomEndpoints: () => { preferenceChanges++; },
    getCustomEndpoints: () => [],
  };
  await assert.rejects(activateModel("llm", "test/model", "rev1", "vulkan", deps), /identity does not match/);
  assert.equal(preferenceChanges, 0);
});


test("vision projector choices are explicit, bounded and forwarded unchanged", async () => {
  const files = [{path:"base.gguf"},{path:"mmproj-a.gguf"},{path:"mmproj-b.gguf"},{path:"mmproj-a.gguf"},{path:"mmproj-00001-of-00002.gguf"}];
  assert.deepEqual(visionProjectorFiles(files),["mmproj-a.gguf","mmproj-b.gguf"]);
  let captured;
  await matchBundles("vision",files,{invoke:async(command,args)=>{captured={command,args};return [];}},"mmproj-b.gguf");
  assert.equal(captured.command,"model_match_bundles");
  assert.equal(captured.args.projectorPath,"mmproj-b.gguf");
  assert.deepEqual(captured.args.files,files);
});
