! ARGUS - modulos que PENGEOM necesita cuando se usa sin PENELOPE.
!
! pengeom.f usa TRACK_mod y PENERROR_mod, que normalmente define penelope.f.
! Son las mismas definiciones que pengeom.f trae comentadas con 'c<<' para
! el uso independiente, de modo que no hace falta modificar pengeom.f.

MODULE TRACK_mod
  SAVE
  DOUBLE PRECISION :: X, Y, Z, U, V, W   ! Posicion y direccion.
  INTEGER*4 :: IBODY, MAT                 ! Cuerpo y material actuales.
  INTEGER*4, DIMENSION(5) :: ILB          ! Etiquetas de historia.
END MODULE TRACK_mod

MODULE PENERROR_mod
  SAVE
  CHARACTER(LEN=128) :: REASON = ' '     ! Mensaje de error/aviso.
  INTEGER*4 :: IRETRN = 0                 ! Codigo de retorno.
END MODULE PENERROR_mod
