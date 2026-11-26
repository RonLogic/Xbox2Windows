ComPtr<ID3D12RootSignature> mesh57_fixture_root(ID3D12Device* guest, ComPtr<ID3DBlob>* serialized = nullptr)
{
    const D3D12_DESCRIPTOR_RANGE srv{D3D12_DESCRIPTOR_RANGE_TYPE_SRV, 1, 0, 0, D3D12_DESCRIPTOR_RANGE_OFFSET_APPEND};
    D3D12_ROOT_PARAMETER params[2]{};
    params[0].ParameterType = D3D12_ROOT_PARAMETER_TYPE_32BIT_CONSTANTS;
    params[0].Constants = {0, 0, 2};
    params[1].ParameterType = D3D12_ROOT_PARAMETER_TYPE_DESCRIPTOR_TABLE;
    params[1].DescriptorTable = {1, &srv};
    return create_root(guest, {2, params, 0, nullptr, D3D12_ROOT_SIGNATURE_FLAG_NONE}, serialized);
}

bool run_mesh57_runtime(ID3D12Device* device, ID3D12Device* guest, ID3D12RootSignature* writer_root,
                        const std::filesystem::path& exchange, const std::wstring& log_path)
{
    constexpr UINT side = 32, geometry_size = 512;
    const auto read = [&](const char* name) {
        std::ifstream file(exchange / name, std::ios::binary);
        return std::vector<char>(std::istreambuf_iterator<char>(file), {});
    };
    const auto ms = read("ms.bin"), ps = read("ps.bin"), geometry = read("geometry.bin");
    if (ms.empty() || ps.empty() || geometry.size() != geometry_size) {
        std::fputs("slot 57 fixture is missing its shader pair or geometry\n", stderr);
        return false;
    }
    const auto cache = exchange / "requests";
    check(SetEnvironmentVariableW(L"X2W_SHADER_CACHE", cache.c_str()) ? S_OK : E_FAIL, "set slot 57 runtime cache");
    const auto root = mesh57_fixture_root(guest);
    std::array<UINT64, 0x300 / sizeof(UINT64)> fields{};
    fields[0] = reinterpret_cast<UINT64>(root.Get());
    fields[5] = reinterpret_cast<UINT64>(ps.data());
    fields[6] = ps.size();
    fields[13] = reinterpret_cast<UINT64>(ms.data());
    fields[14] = ms.size();
    using Create = HRESULT (WINAPI*)(IUnknown*, const void*, REFIID, void**);
    ComPtr<IUnknown> pso;
    check(slot<Create>(guest, 57)(guest, fields.data(), __uuidof(ID3D12PipelineState),
        reinterpret_cast<void**>(pso.GetAddressOf())), "create fixed slot 57 mesh pipeline");
    const D3D12_INDIRECT_ARGUMENT_DESC argument{D3D12_INDIRECT_ARGUMENT_TYPE_DISPATCH_MESH};
    const D3D12_COMMAND_SIGNATURE_DESC signature_desc{sizeof(D3D12_DISPATCH_MESH_ARGUMENTS), 1, &argument, 0};
    ComPtr<ID3D12CommandSignature> signature;
    check(guest->CreateCommandSignature(&signature_desc, nullptr, IID_PPV_ARGS(&signature)), "create slot 57 signature");

    PlacedBuffer arguments(device, guest, static_cast<UINT>(sizeof(initial)), D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS);
    const auto vertex_guest = make_gpu_table_buffer(guest, geometry_size);
    const auto vertex = native_object<ID3D12Resource>(device, guest, vertex_guest.Get());
    const auto upload = make_buffer(device, sizeof(initial) + geometry_size, D3D12_HEAP_TYPE_UPLOAD,
                                    D3D12_RESOURCE_STATE_GENERIC_READ);
    const D3D12_RANGE empty{0, 0};
    void* mapped{};
    check(upload->Map(0, &empty, &mapped), "map slot 57 fixture inputs");
    std::memcpy(mapped, initial.data(), sizeof(initial));
    std::memcpy(static_cast<char*>(mapped) + sizeof(initial), geometry.data(), geometry.size());
    upload->Unmap(0, nullptr);
    const auto writer = writer_pipeline(device, writer_root, "write_one_mesh_group");
    ComputeCommands producer(device, writer.Get());
    transition(producer.list.Get(), arguments.native.Get(), D3D12_RESOURCE_STATE_COMMON, D3D12_RESOURCE_STATE_COPY_DEST);
    transition(producer.list.Get(), vertex.Get(), D3D12_RESOURCE_STATE_COMMON, D3D12_RESOURCE_STATE_COPY_DEST);
    producer.list->CopyBufferRegion(arguments.native.Get(), 0, upload.Get(), 0, sizeof(initial));
    producer.list->CopyBufferRegion(vertex.Get(), 0, upload.Get(), sizeof(initial), geometry.size());
    transition(producer.list.Get(), vertex.Get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_COMMON);
    transition(producer.list.Get(), arguments.native.Get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
    producer.list->SetComputeRootSignature(writer_root);
    producer.list->SetComputeRootUnorderedAccessView(0, arguments.native->GetGPUVirtualAddress());
    producer.list->Dispatch(1, 1, 1);
    transition(producer.list.Get(), arguments.native.Get(), D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATE_COMMON);
    check(producer.list->Close(), "close slot 57 producer");

    const struct {
        D3D12_DESCRIPTOR_HEAP_DESC desc;
        UINT64 backing, bytes;
    } table_desc{{D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV, 1, D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE}, 0, 0};
    using CreateHeap = HRESULT (WINAPI*)(IUnknown*, const void*, REFIID, void**);
    ComPtr<ID3D12DescriptorHeap> table;
    check(slot<CreateHeap>(guest, 14)(guest, &table_desc, __uuidof(ID3D12DescriptorHeap),
        reinterpret_cast<void**>(table.GetAddressOf())), "create slot 57 table");
    using Handle = UINT64 (WINAPI*)(IUnknown*, void*);
    const auto cpu = slot<Handle>(table.Get(), 9)(table.Get(), nullptr);
    const auto gpu = slot<Handle>(table.Get(), 10)(table.Get(), nullptr);
    check(cpu && gpu ? S_OK : E_FAIL, "get slot 57 table handles");
    D3D12_SHADER_RESOURCE_VIEW_DESC srv{DXGI_FORMAT_R32_TYPELESS, D3D12_SRV_DIMENSION_BUFFER,
                                        D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING};
    srv.Buffer.NumElements = geometry_size / sizeof(UINT);
    srv.Buffer.Flags = D3D12_BUFFER_SRV_FLAG_RAW;
    using View = void (WINAPI*)(IUnknown*, ID3D12Resource*, const D3D12_SHADER_RESOURCE_VIEW_DESC*, UINT64, UINT64);
    slot<View>(guest, 18)(guest, vertex_guest.Get(), &srv, cpu, 0);

    const D3D12_RESOURCE_DESC desc{D3D12_RESOURCE_DIMENSION_TEXTURE2D, 0, side, side, 1, 1, format, {1, 0},
        D3D12_TEXTURE_LAYOUT_UNKNOWN, D3D12_RESOURCE_FLAG_ALLOW_RENDER_TARGET};
    constexpr float clear[]{0, 0, 0, 1};
    const D3D12_CLEAR_VALUE clear_value{format, {0, 0, 0, 1}};
    const auto target = resource(device, desc, D3D12_HEAP_TYPE_DEFAULT, D3D12_RESOURCE_STATE_COMMON, &clear_value);
    const D3D12_DESCRIPTOR_HEAP_DESC rtv_desc{D3D12_DESCRIPTOR_HEAP_TYPE_RTV, 1};
    ComPtr<ID3D12DescriptorHeap> rtv_heap;
    check(device->CreateDescriptorHeap(&rtv_desc, IID_PPV_ARGS(&rtv_heap)), "create slot 57 RTV heap");
    const auto rtv = rtv_heap->GetCPUDescriptorHandleForHeapStart();
    guest->CreateRenderTargetView(target.Get(), nullptr, rtv);
    ComPtr<ID3D12CommandAllocator> allocator;
    check(guest->CreateCommandAllocator(D3D12_COMMAND_LIST_TYPE_DIRECT, IID_PPV_ARGS(&allocator)), "create slot 57 allocator");
    const auto wrapped = begin_guest_list(guest, allocator.Get());
    using Clear = void (WINAPI*)(IUnknown*, SIZE_T, const float*, UINT, const D3D12_RECT*);
    using Targets = void (WINAPI*)(IUnknown*, UINT, const D3D12_CPU_DESCRIPTOR_HANDLE*, BOOL, const D3D12_CPU_DESCRIPTOR_HANDLE*);
    using Bind = void (WINAPI*)(IUnknown*, IUnknown*);
    using Constants = void (WINAPI*)(IUnknown*, UINT, UINT, const UINT*, UINT);
    using Heaps = void (WINAPI*)(IUnknown*, UINT, ID3D12DescriptorHeap* const*);
    using Table = void (WINAPI*)(IUnknown*, UINT, UINT64);
    using Viewport = void (WINAPI*)(IUnknown*, UINT, const D3D12_VIEWPORT*);
    using Scissor = void (WINAPI*)(IUnknown*, UINT, const D3D12_RECT*);
    using Execute = void (WINAPI*)(IUnknown*, IUnknown*, UINT, IUnknown*, UINT64, IUnknown*, UINT64);
    const D3D12_VIEWPORT viewport{0, 0, side, side, 0, 1};
    const D3D12_RECT scissor{0, 0, side, side};
    const UINT counts[]{4, 12};
    ID3D12DescriptorHeap* heaps[]{table.Get()};
    slot<Clear>(wrapped.Get(), 52)(wrapped.Get(), rtv.ptr, clear, 0, nullptr);
    slot<Targets>(wrapped.Get(), 50)(wrapped.Get(), 1, &rtv, FALSE, nullptr);
    slot<Bind>(wrapped.Get(), 25)(wrapped.Get(), pso.Get());
    slot<Bind>(wrapped.Get(), 34)(wrapped.Get(), root.Get());
    slot<Constants>(wrapped.Get(), 40)(wrapped.Get(), 0, 2, counts, 0);
    slot<Heaps>(wrapped.Get(), 32)(wrapped.Get(), 1, heaps);
    slot<Table>(wrapped.Get(), 36)(wrapped.Get(), 1, gpu);
    slot<Viewport>(wrapped.Get(), 21)(wrapped.Get(), 1, &viewport);
    slot<Scissor>(wrapped.Get(), 22)(wrapped.Get(), 1, &scissor);
    slot<Execute>(wrapped.Get(), 61)(wrapped.Get(), signature.Get(), 1, arguments.wrapped.Get(), argument_offset, nullptr, 0);
    check(slot<HRESULT(WINAPI*)(IUnknown*)>(wrapped.Get(), 9)(wrapped.Get()), "close slot 57 consumer");

    const D3D12_COMMAND_QUEUE_DESC queue_desc{};
    ComPtr<ID3D12CommandQueue> queue;
    check(guest->CreateCommandQueue(&queue_desc, IID_PPV_ARGS(&queue)), "create slot 57 DIRECT queue");
    const auto desktop_queue = native_object<ID3D12CommandQueue>(device, guest, queue.Get());
    ComPtr<ID3D12Fence> produced, consumed;
    check(device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&produced)), "create slot 57 producer fence");
    check(device->CreateFence(0, D3D12_FENCE_FLAG_NONE, IID_PPV_ARGS(&consumed)), "create slot 57 consumer fence");
    ID3D12CommandList* lists[]{producer.list.Get()};
    producer.queue->ExecuteCommandLists(1, lists);
    check(producer.queue->Signal(produced.Get(), 1), "signal slot 57 producer fence");
    check(desktop_queue->Wait(produced.Get(), 1), "wait for slot 57 producer on DIRECT queue");
    submit_wrapped(queue.Get(), wrapped.Get());
    check(desktop_queue->Signal(consumed.Get(), 1), "signal slot 57 consumer fence");
    check(mesh_fence_reached(consumed.Get(), 5000) ? S_OK : E_FAIL, "wait for slot 57 consumer");

    D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint{};
    UINT64 bytes{};
    device->GetCopyableFootprints(&desc, 0, 1, 0, &footprint, nullptr, nullptr, &bytes);
    const auto pixels = make_buffer(device, bytes, D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);
    const auto record = make_buffer(device, sizeof(initial), D3D12_HEAP_TYPE_READBACK, D3D12_RESOURCE_STATE_COPY_DEST);
    ComputeCommands copy(device, nullptr, D3D12_COMMAND_LIST_TYPE_DIRECT);
    transition(copy.list.Get(), target.Get(), D3D12_RESOURCE_STATE_COMMON, D3D12_RESOURCE_STATE_COPY_SOURCE);
    const D3D12_TEXTURE_COPY_LOCATION src{target.Get(), D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX, {}};
    D3D12_TEXTURE_COPY_LOCATION dst{pixels.Get(), D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT, {}};
    dst.PlacedFootprint = footprint;
    copy.list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    copy.list->CopyBufferRegion(record.Get(), 0, arguments.native.Get(), 0, sizeof(initial));
    copy.submit_and_wait(device);
    const D3D12_RANGE record_range{0, sizeof(initial)};
    check(record->Map(0, &record_range, &mapped), "map slot 57 argument guards");
    Words observed{};
    std::memcpy(observed.data(), mapped, sizeof(observed));
    record->Unmap(0, &empty);
    Words expected = initial;
    expected[1] = expected[2] = expected[3] = 1;
    if (observed != expected) {
        std::fputs("slot 57 GPU DispatchMesh record or guard changed\n", stderr);
        return false;
    }
    const D3D12_RANGE pixels_range{0, static_cast<SIZE_T>(bytes)};
    check(pixels->Map(0, &pixels_range, &mapped), "map slot 57 color readback");
    std::ofstream output(exchange / "pixels.bin", std::ios::binary);
    for (UINT y = 0; y < side; ++y)
        output.write(static_cast<const char*>(mapped) + y * footprint.Footprint.RowPitch, side * sizeof(UINT));
    pixels->Unmap(0, &empty);
    output.close();
    const auto census = indirect_line(guest, log_path);
    std::puts(census.c_str());
    DeleteFileW(log_path.c_str());
    return output && !census.empty();
}
