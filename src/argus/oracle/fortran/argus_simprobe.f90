! ARGUS - sonda de las comprobaciones de penhmain (PMRDR0 + PMRDR) sin simular.
!
! Se enlaza con penhmain.f del usuario sin su programa principal y con
! PEINIT/PHINIT (y las funciones fisicas que PMRDR usa con FORCER < 0)
! sustituidas por XEINIT/XHINIT..., que no hacen nada: no hacen falta los
! ficheros de material. PENGEOM (GEOMIN) es el real. Lee el .in por la
! entrada estandar, deja penhmain.dat en el directorio de trabajo y escribe
! por la salida estandar el codigo de retorno.

PROGRAM ARGUS_SIMPROBE
  USE PENHMAIN_input
  USE PENERROR_mod
  IMPLICIT NONE
  CALL PMRDR0
  IF (IRETRN == 0) CALL PMRDR
  WRITE(6,'(A,I8)') 'IRETRN ', IRETRN
  WRITE(6,'(A,A)') 'REASON ', TRIM(REASON)
END PROGRAM ARGUS_SIMPROBE
