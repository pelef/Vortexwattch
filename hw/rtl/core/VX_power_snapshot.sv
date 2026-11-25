// SPDX-License-Identifier: Apache-2.0
`include "VX_define.vh"

module VX_power_snapshot import VX_gpu_pkg::*; #(
    parameter CORE_ID = 0
) (
    input wire clk,
    input wire reset,
    input wire capture,
    input wire [7:0] bank,
    input wire [PERF_CTR_BITS-1:0] cycles,
    input pipeline_perf_t pipeline_perf,
    input sysmem_perf_t sysmem_perf,
    output logic [`VX_POWER_SNAPSHOT_WORDS-1:0][PERF_CTR_BITS-1:0] values
);
    `UNUSED_VAR(pipeline_perf)
    `UNUSED_VAR(sysmem_perf)
    localparam SOCKET_ID = CORE_ID / `SOCKET_SIZE;
    localparam CLUSTER_ID = SOCKET_ID / NUM_SOCKETS;
    logic [`VX_POWER_SNAPSHOT_WORDS-1:0][PERF_CTR_BITS-1:0] live;
    always @(*) begin
        live = '0;
        live[0] = cycles;
        live[24] = PERF_CTR_BITS'(SOCKET_ID);
        live[25] = PERF_CTR_BITS'(CLUSTER_ID);
        live[26] = PERF_CTR_BITS'(`MEM_BLOCK_SIZE);
        live[27] = PERF_CTR_BITS'(PERF_CTR_BITS);
        case (bank)
    `ifdef POWER_ENABLE
        `VX_DCR_MPM_CLASS_POWER: begin
            for (integer i = 0; i < `VX_POWER_NUM_ACTIVITY; ++i)
                live[i+1] = pipeline_perf.power.activity[i];
            live[10] = pipeline_perf.ifetches;
            live[11] = pipeline_perf.loads;
            live[12] = pipeline_perf.stores;
        end
        `VX_DCR_MPM_CLASS_POWER_WARPS: begin
            for (integer i = 0; i < `VX_POWER_NUM_OPS; ++i)
                live[i+1] = pipeline_perf.power.warp_instructions[i];
        end
        `VX_DCR_MPM_CLASS_POWER_LANES: begin
            for (integer i = 0; i < `VX_POWER_NUM_OPS; ++i)
                live[i+1] = pipeline_perf.power.lane_instructions[i];
        end
        `VX_DCR_MPM_CLASS_POWER_ISSUE: begin
            for (integer i = 0; i < `VX_POWER_NUM_OPS; ++i)
                live[i+1] = pipeline_perf.power.issue_cycles[i];
        end
    `endif
        `VX_DCR_MPM_CLASS_POWER_MEM: begin
            live[1] = sysmem_perf.icache.reads;
            live[2] = sysmem_perf.icache.read_misses;
            live[3] = sysmem_perf.dcache.reads;
            live[4] = sysmem_perf.dcache.writes;
            live[5] = sysmem_perf.dcache.read_misses;
            live[6] = sysmem_perf.dcache.write_misses;
            live[7] = sysmem_perf.l2cache.reads;
            live[8] = sysmem_perf.l2cache.writes;
            live[9] = sysmem_perf.l2cache.read_misses;
            live[10] = sysmem_perf.l2cache.write_misses;
            live[11] = sysmem_perf.l3cache.reads;
            live[12] = sysmem_perf.l3cache.writes;
            live[13] = sysmem_perf.l3cache.read_misses;
            live[14] = sysmem_perf.l3cache.write_misses;
            live[15] = sysmem_perf.lmem.reads;
            live[16] = sysmem_perf.lmem.writes;
            live[17] = sysmem_perf.mem.reads;
            live[18] = sysmem_perf.mem.writes;
        end
        default:;
        endcase
    end

    always @(posedge clk) begin
        if (reset)
            values <= '0;
        else if (capture)
            values <= live;
    end
endmodule
