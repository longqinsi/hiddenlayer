"""
HiddenLayer

PyTorch graph importer.
 
Written by Waleed Abdulla
Licensed under the MIT License
"""

from __future__ import absolute_import, division, print_function
import re
from .graph import Graph, Node
from . import transforms as ht
import torch

# PyTorch Graph Transforms
FRAMEWORK_TRANSFORMS = [
    # Hide onnx: prefix
    ht.Rename(op=r"onnx::(.*)", to=r"\1"),
    # ONNX uses Gemm for linear layers (stands for General Matrix Multiplication).
    # It's an odd name that noone recognizes. Rename it. 
    ht.Rename(op=r"Gemm", to=r"Linear"),
    # PyTorch layers that don't have an ONNX counterpart
    ht.Rename(op=r"aten::max\_pool2d\_with\_indices", to="MaxPool"),
    # Shorten op name
    ht.Rename(op=r"BatchNormalization", to="BatchNorm"),
]


def dump_pytorch_graph(graph):
    """List all the nodes in a PyTorch graph."""
    f = "{:25} {:40}   {} -> {}"
    print(f.format("kind", "scopeName", "inputs", "outputs"))
    for node in graph.nodes():
        print(f.format(node.kind(), node.scopeName(),
                       [i.unique() for i in node.inputs()],
                       [i.unique() for i in node.outputs()]
                       ))


def pytorch_id(node):
    """Returns a unique ID for a node."""
    # After ONNX simplification, the scopeName is not unique anymore
    # so append node outputs to guarantee uniqueness
    return node.scopeName() + "/outputs/" + "/".join(["{}".format(o.unique()) for o in node.outputs()])


def get_shape(torch_node):
    """Return the output shape of the given Pytorch node."""
    # Extract node output shape from the node string representation
    # This is a hack because there doesn't seem to be an official way to do it.
    # See my quesiton in the PyTorch forum:
    # https://discuss.pytorch.org/t/node-output-shape-from-trace-graph/24351/2
    # TODO: find a better way to extract output shape
    # TODO: Assuming the node has one output. Update if we encounter a multi-output node.
    m = re.match(r".*Float\(([\d\s\,]+)\).*", str(next(torch_node.outputs())))
    if m:
        shape = m.group(1)
        shape = shape.split(",")
        shape = tuple(map(int, shape))
    else:
        shape = None
    return shape


def import_graph(hl_graph, model, args, input_names=None, verbose=False):
    # TODO: add input names to graph

    # Run the Pytorch graph to get a trace and generate a graph from it
    trace, out = torch.jit._get_trace_graph(model, args)
    ### modified by longqinsi 2023/05/12 适配PyTorch 2.0.1
    # torch_graph = torch.onnx._optimize_trace(trace, torch.onnx.OperatorExportTypes.ONNX)
    torch_graph = torch.onnx._optimize_graph(trace, torch.onnx.OperatorExportTypes.ONNX)
    ### end modified

    # Dump list of nodes (DEBUG only)
    if verbose:
        dump_pytorch_graph(torch_graph)

    params = {}
    # Loop through nodes and build HL graph
    for torch_node in torch_graph.nodes():
        # Op
        op = torch_node.kind()
        print("torch_node.kind(): " + op)
        # Parameters
        if op == 'onnx::Gemm':
            import re
            params_str = re.search(r'(?<=onnx::Gemm\[)[^\[\]]+', str(torch_node)).group()
            print("onnx::Gemm[" + params_str + "]")
            for item in params_str.split(','):
                k, v = item.split('=')
                k = k.strip()
                v = v.strip()
                if k in ['alpha', 'beta']:
                    params[k] = float(v)
                elif k in ['transB']:
                    params[k] = int(v)

        # params = {k: torch_node[k] for k in torch_node.attributeNames()}
        # Inputs/outputs
        # TODO: inputs = [i.unique() for i in node.inputs()]
        outputs = [o.unique() for o in torch_node.outputs()]
        # Get output shape
        shape = get_shape(torch_node)
        # Add HL node
        hl_node = Node(uid=pytorch_id(torch_node), name=None, op=op, 
                       output_shape=shape, params=params)
        hl_graph.add_node(hl_node)
        # Add edges
        for target_torch_node in torch_graph.nodes():
            target_inputs = [i.unique() for i in target_torch_node.inputs()]
            if set(outputs) & set(target_inputs):
                hl_graph.add_edge_by_id(pytorch_id(torch_node), pytorch_id(target_torch_node), shape)
    return hl_graph
