//! adiskreader_native: native versions of the decompressors of adiskreader.compression.
//! Ports of the Python decoders (xpress.py, lzx.py, lzms.py, lz4.py, veeamrle.py), they accept and reject the same input.
//! adiskreader uses this module when it can be imported, nothing else is needed.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

mod bits;
mod crc;
mod huffman;
mod lz4;
mod lzms;
mod lzx;
mod veeamrle;
mod xpress;

/// larger than any chunk of a WIM file or block of a backup file, the output is allocated before decompressing
const MAX_OUT_SIZE: usize = 1 << 30;

fn check_out_size(out_size: usize) -> PyResult<()> {
    if out_size > MAX_OUT_SIZE {
        return Err(PyValueError::new_err("Output size is too large"));
    }
    Ok(())
}

/// Decompresses one XPRESS (Huffman) chunk, out_size is the size of the result
#[pyfunction]
fn xpress_decompress<'py>(py: Python<'py>, data: &[u8], out_size: usize) -> PyResult<Bound<'py, PyBytes>> {
    check_out_size(out_size)?;
    let res = py.detach(|| xpress::decompress(data, out_size));
    match res {
        Ok(out) => Ok(PyBytes::new(py, &out)),
        Err(e) => Err(PyValueError::new_err(e)),
    }
}

/// Decompresses one LZX chunk of a WIM file, out_size is the size of the result, window_size is the chunk size
#[pyfunction]
#[pyo3(signature = (data, out_size, window_size=32768))]
fn lzx_decompress<'py>(py: Python<'py>, data: &[u8], out_size: usize, window_size: usize) -> PyResult<Bound<'py, PyBytes>> {
    check_out_size(out_size)?;
    let res = py.detach(|| lzx::decompress(data, out_size, window_size));
    match res {
        Ok(out) => Ok(PyBytes::new(py, &out)),
        Err(e) => Err(PyValueError::new_err(e)),
    }
}

/// Decompresses one LZMS chunk, out_size is the size of the result
#[pyfunction]
fn lzms_decompress<'py>(py: Python<'py>, data: &[u8], out_size: usize) -> PyResult<Bound<'py, PyBytes>> {
    check_out_size(out_size)?;
    let res = py.detach(|| lzms::decompress(data, out_size));
    match res {
        Ok(out) => Ok(PyBytes::new(py, &out)),
        Err(e) => Err(PyValueError::new_err(e)),
    }
}

/// Decompresses one LZ4 block (no frame), out_size is the size of the result
#[pyfunction]
fn lz4_decompress<'py>(py: Python<'py>, data: &[u8], out_size: usize) -> PyResult<Bound<'py, PyBytes>> {
    check_out_size(out_size)?;
    let res = py.detach(|| lz4::decompress(data, out_size));
    match res {
        Ok(out) => Ok(PyBytes::new(py, &out)),
        Err(e) => Err(PyValueError::new_err(e)),
    }
}

/// Decompresses the run length encoded data of a block of a Veeam backup file, out_size is the size of the result
#[pyfunction]
fn veeam_rle_decompress<'py>(py: Python<'py>, data: &[u8], out_size: usize) -> PyResult<Bound<'py, PyBytes>> {
    check_out_size(out_size)?;
    let res = py.detach(|| veeamrle::decompress(data, out_size));
    match res {
        Ok(out) => Ok(PyBytes::new(py, &out)),
        Err(e) => Err(PyValueError::new_err(e)),
    }
}

/// CRC-32C (Castagnoli) of the data
#[pyfunction]
#[pyo3(signature = (data, crc=0))]
fn crc32c(py: Python<'_>, data: &[u8], crc: u32) -> u32 {
    py.detach(|| crc::crc32c(data, crc))
}

#[pymodule]
fn adiskreader_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(xpress_decompress, m)?)?;
    m.add_function(wrap_pyfunction!(lzx_decompress, m)?)?;
    m.add_function(wrap_pyfunction!(lzms_decompress, m)?)?;
    m.add_function(wrap_pyfunction!(lz4_decompress, m)?)?;
    m.add_function(wrap_pyfunction!(veeam_rle_decompress, m)?)?;
    m.add_function(wrap_pyfunction!(crc32c, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
