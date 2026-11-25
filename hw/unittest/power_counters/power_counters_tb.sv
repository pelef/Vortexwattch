// SPDX-License-Identifier: Apache-2.0
`include "VX_define.vh"

module power_counters_tb;
    import VX_gpu_pkg::*;
    logic clk = 0;
    always #5 clk <= ~clk;
    logic reset = 1;
    logic busy = 0;
    logic [`NUM_WARPS-1:0] active_warps = '0;
    logic [`NUM_WARPS-1:0][`NUM_THREADS-1:0] thread_masks = '0;
    logic decode_fire = 0;
    decode_t decoded;
    logic [NUM_EX_UNITS*`ISSUE_WIDTH-1:0] dispatch_fire = '0;
    dispatch_t dispatched [NUM_EX_UNITS*`ISSUE_WIDTH];
    logic [`ISSUE_WIDTH-1:0] writeback_valid = '0;
    writeback_t written [`ISSUE_WIDTH];
    power_perf_t perf;

    VX_power_counters #(.COUNTER_BITS(8)) dut (.*);

    pipeline_perf_t pipeline_perf;
    sysmem_perf_t sysmem_perf;
    base_dcrs_t base_dcrs;
    logic [PERF_CTR_BITS-1:0] cycles = 44'h123456789ab;
    logic read_enable = 0;
    logic [`VX_CSR_ADDR_BITS-1:0] read_addr = 0;
    wire [`XLEN-1:0] read_data_ro, read_data_rw;
    VX_commit_csr_if commit_csr_if();
    assign commit_csr_if.instret = '0;
`ifdef EXT_F_ENABLE
    VX_fpu_csr_if fpu_csr_if [`NUM_FPU_BLOCKS]();
    for (genvar i = 0; i < `NUM_FPU_BLOCKS; ++i) begin : g_fpu
        assign fpu_csr_if[i].write_enable = 0;
        assign fpu_csr_if[i].write_wid = '0;
        assign fpu_csr_if[i].write_fflags = '0;
        assign fpu_csr_if[i].read_wid = '0;
        `UNUSED_VAR(fpu_csr_if[i].read_frm)
    end
`endif
    VX_csr_data #(.CORE_ID(10)) csr (
        .clk(clk), .reset(reset), .base_dcrs(base_dcrs),
        .sysmem_perf(sysmem_perf), .pipeline_perf(pipeline_perf),
        .commit_csr_if(commit_csr_if),
`ifdef EXT_F_ENABLE
        .fpu_csr_if(fpu_csr_if),
`endif
        .cycles(cycles), .active_warps(active_warps), .thread_masks(thread_masks),
        .read_enable(read_enable), .read_uuid('0), .read_wid('0),
        .read_addr(read_addr), .read_data_ro(read_data_ro), .read_data_rw(read_data_rw),
        .write_enable(1'b0), .write_uuid('0), .write_wid('0), .write_addr('0), .write_data('0)
    );
    `UNUSED_VAR(read_data_rw)

    always @(*) begin
        pipeline_perf = '0;
        pipeline_perf.power = perf;
        pipeline_perf.ifetches = 44'd17;
    end

    task automatic tick;
        @(posedge clk); #1;
        @(negedge clk); #1;
    endtask

    task automatic clear;
        reset = 1;
        dispatch_fire = '0;
        decode_fire = 0;
        writeback_valid = '0;
        busy = 0;
        active_warps = '0;
        thread_masks = '0;
        decoded = '0;
        for (integer p = 0; p < NUM_EX_UNITS*`ISSUE_WIDTH; ++p)
            dispatched[p] = '0;
        for (integer p = 0; p < `ISSUE_WIDTH; ++p)
            written[p] = '0;
        tick();
        reset = 0;
    endtask

    task automatic check_op(input integer unit_id, input logic [3:0] op,
                            input op_args_t args, input integer index);
        integer port_id;
        clear();
        port_id = unit_id * `ISSUE_WIDTH;
        dispatched[port_id].op_type = op;
        dispatched[port_id].op_args = args;
        dispatched[port_id].tmask = `SIMD_WIDTH'(3);
        dispatched[port_id].sop = 1;
        dispatch_fire[port_id] = 1;
        tick();
        assert (perf.warp_instructions[index] == 1) else $fatal(1, "warp class %0d", index);
        assert (perf.lane_instructions[index] == 2) else $fatal;
        assert (perf.issue_cycles[index] == 1) else $fatal;
        dispatch_fire = '0; // stalled/invalid packet must not be counted
        tick();
        assert (perf.warp_instructions[index] == 1) else $fatal;
        dispatched[port_id].sop = 0; // second SIMD packet, not a second instruction
        dispatched[port_id].tmask = `SIMD_WIDTH'(1);
        dispatch_fire[port_id] = 1;
        tick();
        assert (perf.warp_instructions[index] == 1 && perf.lane_instructions[index] == 3) else $fatal;
        dispatched[port_id].sop = 1;
        dispatched[port_id+1] = dispatched[port_id];
        dispatch_fire[port_id+1] = 1;
        tick();
        assert (perf.warp_instructions[index] == 3 && perf.lane_instructions[index] == 5) else $fatal;
        assert (perf.issue_cycles[index] == 3) else $fatal(1, "issue cycles count ports twice");
        assert (perf.activity[`VX_POWER_ISSUED_WARPS] == 3) else $fatal;
        assert (perf.activity[`VX_POWER_ISSUED_LANES] == 5) else $fatal;
        dispatch_fire = '0;
    endtask

    task automatic read_csr(input logic [11:0] addr, input logic [`XLEN-1:0] expected);
        read_addr = addr;
        read_enable = 1;
        #1;
        assert (read_data_ro == expected) else
            $fatal(1, "CSR %h got %h expected %h", addr, read_data_ro, expected);
        tick();
        read_enable = 0;
    endtask

    op_args_t args;
    initial begin
        base_dcrs = '0;
        sysmem_perf = '0;
        args = '0;
        assert (`ISSUE_WIDTH >= 2 && `SIMD_WIDTH >= 2 && `NUM_THREADS >= 3) else $fatal;
        args.alu.xtype = ALU_TYPE_ARITH;
        check_op(EX_ALU, INST_ALU_ADD, args, `VX_POWER_OP_INT_ALU);
        args.alu.xtype = ALU_TYPE_BRANCH;
        check_op(EX_ALU, INST_BR_BEQ, args, `VX_POWER_OP_BRANCH);
        args.alu.xtype = ALU_TYPE_MULDIV;
        check_op(EX_ALU, 4'd0, args, `VX_POWER_OP_INT_MUL);
        check_op(EX_ALU, 4'd4, args, `VX_POWER_OP_INT_DIV);
        args = '0;
        check_op(EX_LSU, 4'd0, args, `VX_POWER_OP_LOAD);
        args.lsu.is_store = 1;
        check_op(EX_LSU, 4'd0, args, `VX_POWER_OP_STORE);
        check_op(EX_LSU, 4'hf, args, `VX_POWER_OP_FENCE);
        args = '0;
        check_op(EX_SFU, INST_SFU_CSRRW, args, `VX_POWER_OP_CSR);
        check_op(EX_SFU, INST_SFU_TMC, args, `VX_POWER_OP_WARP_CONTROL);
`ifdef EXT_F_ENABLE
        for (integer fmt = 0; fmt < 2; ++fmt) begin
            args.fpu.fmt = 2'(fmt);
            check_op(EX_FPU, INST_FPU_ADD, args, `VX_POWER_OP_FP32_ADD + fmt * 6);
            check_op(EX_FPU, INST_FPU_MUL, args, `VX_POWER_OP_FP32_MUL + fmt * 6);
            check_op(EX_FPU, INST_FPU_MADD, args, `VX_POWER_OP_FP32_FMA + fmt * 6);
            check_op(EX_FPU, INST_FPU_NMADD, args, `VX_POWER_OP_FP32_FMA + fmt * 6);
            check_op(EX_FPU, INST_FPU_DIV, args, `VX_POWER_OP_FP32_DIV + fmt * 6);
            check_op(EX_FPU, INST_FPU_SQRT, args, `VX_POWER_OP_FP32_SQRT + fmt * 6);
            check_op(EX_FPU, INST_FPU_MISC, args, `VX_POWER_OP_FP32_MISC + fmt * 6);
        end
`endif
`ifdef EXT_TCU_ENABLE
        check_op(EX_TCU, 4'd0, args, `VX_POWER_OP_TENSOR);
`endif
        clear();
        busy = 1;
        active_warps = `NUM_WARPS'(3);
        thread_masks[0] = `NUM_THREADS'(7);
        thread_masks[1] = `NUM_THREADS'(1);
        decoded.used_rs = 3'b101;
        decoded.tmask = `NUM_THREADS'(7);
        decode_fire = 1;
        written[0].tmask = `SIMD_WIDTH'(3);
        writeback_valid = 1;
        tick();
        assert (perf.activity[`VX_POWER_BUSY_CYCLES] == 1) else $fatal;
        assert (perf.activity[`VX_POWER_WARP_CYCLES] == 2) else $fatal;
        assert (perf.activity[`VX_POWER_RESIDENT_THREAD_CYCLES] == 4) else $fatal;
        assert (perf.activity[`VX_POWER_LOGICAL_READ_WORDS] == 6) else $fatal;
        assert (perf.activity[`VX_POWER_WRITEBACK_WORDS] == 2) else $fatal;
        decode_fire = 0;
        writeback_valid = 0;
        repeat (255) tick();
        assert (perf.activity[`VX_POWER_BUSY_CYCLES] == 0) else $fatal(1, "wrap");
        clear();
        assert (perf == '0) else $fatal(1, "reset");

        base_dcrs.mpm_class = `VX_DCR_MPM_CLASS_POWER;
        read_csr(`VX_CSR_POWER_ABI, `XLEN'(`VX_POWER_ABI));
        read_csr(`VX_CSR_POWER_SNAPSHOT, `XLEN'(44'h123456789ab));
`ifndef XLEN_64
        read_csr(`VX_CSR_POWER_SNAPSHOT + 12'h80, 32'h123);
        read_csr(`VX_CSR_POWER_ABI + 12'h80, 32'd0);
`endif
        cycles = 44'd5;
        read_csr(`VX_CSR_POWER_SNAPSHOT, `XLEN'(44'h123456789ab)); // frozen
        read_csr(`VX_CSR_POWER_SNAPSHOT + 12'd10, `XLEN'(17));
        read_csr(`VX_CSR_POWER_SNAPSHOT + 12'd24, `XLEN'(10 / `SOCKET_SIZE));
        read_csr(`VX_CSR_POWER_SNAPSHOT + 12'd25, `XLEN'(10 / (`SOCKET_SIZE * NUM_SOCKETS)));
        read_csr(`VX_CSR_POWER_SNAPSHOT + 12'd27, `XLEN'(44));
        base_dcrs.mpm_class = `VX_DCR_MPM_CLASS_POWER_MEM;
        sysmem_perf.mem.reads = 44'habc00000003;
        read_csr(`VX_CSR_POWER_ABI, `XLEN'(`VX_POWER_ABI));
        read_csr(`VX_CSR_POWER_SNAPSHOT, `XLEN'(5));
        read_csr(`VX_CSR_POWER_SNAPSHOT + 12'd17, `XLEN'(44'habc00000003));
`ifndef XLEN_64
        read_csr(`VX_CSR_POWER_SNAPSHOT + 12'd17 + 12'h80, 32'habc);
`endif
        $display("power counters / snapshot / CSR tests passed (XLEN=%0d)", `XLEN);
        $finish;
    end
endmodule
