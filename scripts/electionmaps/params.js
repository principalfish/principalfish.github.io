var params = {

  possibleParams : ["incumbent", "region", "majority"],

  checkParams : function(){

   var parameterInput = false;
   $.each(params.possibleParams, function(i, param){

     var input = getParameterByName(param, url);

     if (input != null){
       if (param == "incumbent"){
         filters.state["current"] = input

       }

       if (param == "region"){
         filters.state["region"] = input
       }

       if (param == "majority"){
         filters.state["majority"][1] = input
       }
       parameterInput = true;
     }


   });


   if (parameterInput == true){
      filters.filter()
   }
  }
}
