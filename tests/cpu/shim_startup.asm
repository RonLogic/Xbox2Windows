; #511's companion still supplies the baseline checks. With a compute request,
; this same EXE owns the allocation branch, dispatch arguments and cleanup.
EXTERN __imp_run_startup_shims:QWORD
EXTERN __imp_compute_request:QWORD
EXTERN __imp_compute_alloc:QWORD
EXTERN __imp_compute_dispatch:QWORD
EXTERN __imp_compute_free:QWORD
EXTERN __imp_draw_request:QWORD
EXTERN __imp_draw_frame:QWORD

.code
native_entry PROC FRAME
    sub rsp, 56
    .allocstack 56
    .endprolog
    call qword ptr [__imp_run_startup_shims]
    cmp eax, 42
    jne done
    call qword ptr [__imp_compute_request]
    test rax, rax
    jz draw_phase
    mov [rsp+32], rax
    mov rcx, rax
    mov edx, 0a8004000h
    call qword ptr [__imp_compute_alloc]
    test rax, rax
    jz no_memory
    mov [rsp+40], rax
    mov rcx, rax
    mov edx, dword ptr [rsp+32]
    xor edx, 012345678h
    mov r8d, 4
    call qword ptr [__imp_compute_dispatch]
    mov [rsp+48], eax
    mov rcx, [rsp+40]
    mov edx, 0a8004000h
    call qword ptr [__imp_compute_free]
    test eax, eax
    jz bad_release
    cmp dword ptr [rsp+48], 0
    je bad_dispatch
draw_phase:
    call qword ptr [__imp_draw_request]
    test eax, eax
    jz success
    cmp eax, 3
    jae draw_two
    mov ecx, eax
    call qword ptr [__imp_draw_frame]
    test eax, eax
    jnz bad_draw
    jmp success
draw_two:
    mov dword ptr [rsp+48], eax
    mov ecx, 3
    call qword ptr [__imp_draw_frame]
    test eax, eax
    jnz bad_draw
    mov ecx, dword ptr [rsp+48]
    add ecx, 1
    call qword ptr [__imp_draw_frame]
    cmp eax, -1
    je bad_second_draw
    test eax, eax
    jnz bad_draw
success:
    mov eax, 42
    jmp done
no_memory:
    mov eax, 17
    jmp done
bad_release:
    mov eax, 18
    jmp done
bad_dispatch:
    mov eax, 19
    jmp done
bad_draw:
    mov eax, 20
    jmp done
bad_second_draw:
    mov eax, 24
done:
    add rsp, 56
    ret
native_entry ENDP
END
